import json
import time
from pathlib import Path

import imageio.v3 as iio
import valkey
from django.core.management.base import BaseCommand, CommandError

import backend.tasks  # noqa: F401 - registers the production plugins
from backend.models import ClusterTimelineItem, PluginRun, PluginRunResult, Timeline, Video
from backend.plugin_manager import PluginManager
from backend.utils import media_path_to_video


PLUGIN_ORDER = [
    "shotdetection",
    "audio_amp",
    "audio_rms",
    "audio_freq",
    "color_analysis",
    "color_brightness_analysis",
    "thumbnail",
    "face_clustering",
    "place_clustering",
    "deepface_emotion",
    "insightface_facesize",
    "places_classification",
    "shot_density",
    "shot_type_classification",
    "shot_angle",
    "shot_level",
    "shot_scale_and_movement",
    "clip",
    "clip_ontology",
    "x_clip",
    "blip_vqa",
    "whisper",
    "whisper_x",
    "audio_emotion",
    "audio_gender",
    "active_speaker_detection",
    "audio_classification",
    "text_ner",
    "text_pos",
    "text_sentiment",
    "ocr_video_detector_onnx",
    "nano_ocr_video",
    "insightface_identification",
    "place_identification",
    "aggregate_scalar",
    "shot_scalar_annotation",
    "invert_scalar",
    "cluster_to_scalar",
]


class Command(BaseCommand):
    help = "Run production plugins sequentially against a video and report timings"

    def add_arguments(self, parser):
        parser.add_argument("--video-id", type=str)
        parser.add_argument("--plugins", nargs="+", type=str)
        parser.add_argument("--output", type=Path)
        parser.add_argument("--clear-analyser-cache", action="store_true")
        parser.add_argument("--keep-results", action="store_true")

    def parameters_for(self, plugin, video, query_image, concept_csv):
        shot_timeline = Timeline.objects.filter(
            video=video, type=Timeline.TYPE_ANNOTATION, name="Shots"
        ).filter(timelinesegment__isnull=False).distinct().last()
        scalar_timeline = Timeline.objects.filter(
            video=video,
            type=Timeline.TYPE_PLUGIN_RESULT,
            plugin_run_result__type=PluginRunResult.TYPE_SCALAR,
        ).first()
        cluster_item = ClusterTimelineItem.objects.filter(video=video).first()

        values = {
            "audio_classification": {"segment_type": "Speaker"},
            "blip_vqa": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None,
                "query_term": "Describe the scene",
            },
            "clip": {"search_term": "person"},
            "clip_ontology": {
                "concept_csv": str(concept_csv),
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None,
            },
            "insightface_identification": {"query_images": str(query_image)},
            "nano_ocr_video": {"fps": 0.05},
            "ocr_video_detector_onnx": {"fps": 0.1},
            "place_identification": {"query_images": str(query_image)},
            "place_clustering": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "shot_density": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "shot_type_classification": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "shot_angle": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "shot_level": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "shot_scale_and_movement": {
                "shot_timeline_id": shot_timeline.id.hex if shot_timeline else None
            },
            "x_clip": {"search_term": "person"},
        }
        if scalar_timeline:
            scalar_id = scalar_timeline.id.hex
            values.update(
                {
                    "aggregate_scalar": {"timeline_ids": [scalar_id]},
                    "invert_scalar": {"scalar_timeline_id": scalar_id},
                    "shot_scalar_annotation": {
                        "shot_timeline_id": shot_timeline.id.hex,
                        "scalar_timeline_id": scalar_id,
                    },
                }
            )
        if cluster_item:
            values["cluster_to_scalar"] = {
                "cluster_timeline_item_id": cluster_item.id.hex
            }

        return [
            {"name": name, "value": value}
            for name, value in values.get(plugin, {}).items()
            if value is not None
        ]

    def handle(self, *args, **options):
        if options["video_id"]:
            try:
                video = Video.objects.get(pk=options["video_id"])
            except Video.DoesNotExist as exc:
                raise CommandError("Video does not exist") from exc
        else:
            video = Video.objects.order_by("-date").first()
            if video is None:
                raise CommandError("No uploaded video is available")

        manager = PluginManager()
        selected = options["plugins"] or PLUGIN_ORDER
        unknown = sorted(set(selected) - manager._plugins.keys())
        if unknown:
            raise CommandError(f"Unknown plugins: {', '.join(unknown)}")

        if options["clear_analyser_cache"]:
            cache = valkey.Valkey(host="valkey", port=6380, db=1)
            keys = list(cache.scan_iter("analyser:*", count=500))
            if keys:
                cache.delete(*keys)
            self.stdout.write(f"Cleared {len(keys)} analyser result-cache entries")

        query_image = Path("/tmp/tibava-plugin-audit-query.jpg")
        concept_csv = Path("/tmp/tibava-plugin-audit-concepts.csv")
        video_path = media_path_to_video(video.file.hex, video.ext)
        metadata = iio.immeta(video_path)
        midpoint = int(video.duration * float(metadata.get("fps", 25)) / 2)
        query_image.write_bytes(
            iio.imwrite(
                "<bytes>", iio.imread(video_path, index=midpoint), extension=".jpg"
            )
        )
        concept_csv.write_text("Person,person\nInterior,interior room\n", encoding="utf-8")

        existing_runs = set(PluginRun.objects.filter(video=video).values_list("id", flat=True))
        results = []
        try:
            for index, plugin in enumerate(selected, start=1):
                self.stdout.write(f"[{index}/{len(selected)}] {plugin}")
                started = time.perf_counter()
                try:
                    result = manager(
                        plugin,
                        parameters=self.parameters_for(
                            plugin, video, query_image, concept_csv
                        ),
                        user=video.owner,
                        video=video,
                        run_async=False,
                        dry_run=False,
                    )
                    ok = bool(result.get("status"))
                    error = None if ok else "plugin returned status=false"
                except Exception as exc:
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"

                entry = {
                    "plugin": plugin,
                    "ok": ok,
                    "seconds": round(time.perf_counter() - started, 3),
                    "error": error,
                }
                results.append(entry)
                self.stdout.write(json.dumps(entry, sort_keys=True))
        finally:
            if not options["keep_results"]:
                PluginRun.objects.filter(video=video).exclude(id__in=existing_runs).delete()
            query_image.unlink(missing_ok=True)
            concept_csv.unlink(missing_ok=True)

        report = {
            "video_id": video.id.hex,
            "video_duration": video.duration,
            "plugins": results,
        }
        if options["output"]:
            options["output"].parent.mkdir(parents=True, exist_ok=True)
            options["output"].write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
            )

        failures = [result for result in results if not result["ok"]]
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed {len(results) - len(failures)}/{len(results)} plugins"
            )
        )
        if failures:
            names = ", ".join(result["plugin"] for result in failures)
            raise CommandError(f"Plugin audit failed: {names}")
