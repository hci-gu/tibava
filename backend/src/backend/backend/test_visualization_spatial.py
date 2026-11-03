import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import numpy as np
from PIL import Image
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError

from data import Annotation, BboxData, DataManager, ImageEmbedding, KpsData, StringData
from backend.models import ClusterItem, ClusterTimelineItem, PluginRun, PluginRunResult, Timeline, TimelineSegment, Video
from backend.utils.spatial_results import save_spatial_result
from backend.utils.visualization_export import available_sources, build_snapshot, validate_configuration
from backend.utils.visualization_compositor import OverlayScene, display_box
from backend.utils.visualization_spatial import link_speaker_clusters
from backend.utils.visualization_render import render_video, RenderCancelled
from utils.spatial_tracking import assign_tracks
from backend.tasks.ocr import OCRTask


class SpatialSnapshotTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(DATA_OUTPUT_PATH=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)
        self.manager = DataManager(self.directory.name)
        self.user = get_user_model().objects.create(username="spatial-owner")
        self.video = Video.objects.create(owner=self.user, name="Spatial demo", duration=10, ext=".mp4")
        self.run = PluginRun.objects.create(video=self.video, type="face_clustering", status=PluginRun.STATUS_DONE)
        self.cluster_result = PluginRunResult.objects.create(plugin_run=self.run, type=PluginRunResult.TYPE_CLUSTER, name="clusters")
        self.cluster = ClusterTimelineItem.objects.create(video=self.video, plugin_run=self.run, name="Edited person")
        with self.manager.create_data("ImageEmbeddings") as data:
            embedding = ImageEmbedding(ref_id="face-1", time=1, delta_time=0.5, embedding=np.ones(4))
            data.embeddings.append(embedding)
        PluginRunResult.objects.create(plugin_run=self.run, name="features", type=PluginRunResult.TYPE_IMAGE_EMBEDDINGS, data_id=data.id)
        self.item = ClusterItem.objects.create(video=self.video, cluster_timeline_item=self.cluster,
                                              plugin_run_result=self.cluster_result, embedding_id=embedding.id, time=1, delta_time=0.5)

    def geometry(self):
        with self.manager.create_data("BboxesData") as boxes:
            boxes.bboxes.append(BboxData(ref_id="face-1", time=1, delta_time=0.5, x=0.1, y=0.2, w=0.2, h=0.3, det_score=0.9))
        PluginRunResult.objects.create(plugin_run=self.run, name="bboxes", type=PluginRunResult.TYPE_BBOXES, data_id=boxes.id)
        with self.manager.create_data("KpssData") as points:
            points.kpss.append(KpsData(ref_id="face-1", time=1, delta_time=0.5, x=[0.15, 0.25], y=[0.3, 0.3]))
        PluginRunResult.objects.create(plugin_run=self.run, name="kpss", type=PluginRunResult.TYPE_KEYPOINTS, data_id=points.id)

    def snapshot(self, mode="cluster_cards", source_id=None, **options):
        layer = {"source_id": source_id or f"clusters:{self.run.id.hex}", "mode": mode, **options}
        config = validate_configuration(self.video, {"start": 1.1, "end": 3, "layers": [layer]}, available_sources(self.video))
        return build_snapshot(self.video, config, False)

    def test_older_runs_offer_cards_and_reject_boxes(self):
        source = available_sources(self.video)[0]
        self.assertEqual(source["modes"], ["cluster_cards"])
        self.assertIn("older run", source["notice"])
        self.assertEqual(self.snapshot()["layers"][0]["records"][0]["label"], "Edited person")
        with self.assertRaisesMessage(ValueError, "Unsupported"):
            self.snapshot("face_boxes")

    def test_boxes_join_current_cluster_memberships_with_landmarks(self):
        self.geometry()
        moved = ClusterTimelineItem.objects.create(video=self.video, plugin_run=self.run, name="Moved face")
        self.item.cluster_timeline_item = moved
        self.item.save()
        snapshot = self.snapshot("face_boxes", cluster_ids=[moved.id.hex], show_landmarks=True)
        record = snapshot["layers"][0]["records"][0]
        self.assertEqual(record["label"], "Moved face")
        self.assertEqual(record["identity"], moved.id.hex)
        self.assertAlmostEqual(record["time"], -0.1)
        self.assertEqual(record["keypoints"], [[0.15, 0.3, 1], [0.25, 0.3, 1]])
        self.item.delete()
        with self.assertRaisesMessage(ValueError, "No detections"):
            self.snapshot("face_boxes", cluster_ids=[moved.id.hex])
        self.assertEqual(record["label"], "Moved face")

    def test_cluster_thumbnail_is_frozen_and_paths_are_confined(self):
        name = "a" * 32 + ".jpg"
        path = Path(self.directory.name) / "aa/aa" / name
        path.parent.mkdir(parents=True)
        Image.new("RGB", (200, 200), "red").save(path)
        self.item.image_path = "/predictions/aa/aa/" + name
        self.item.save()
        snapshot = self.snapshot()
        self.assertTrue(snapshot["layers"][0]["cards"][0]["thumbnail"])
        path.unlink()
        self.assertTrue(snapshot["layers"][0]["cards"][0]["thumbnail"])

    def test_smoothing_requires_shots_and_hold_stops_at_cut(self):
        self.geometry()
        with self.assertRaisesMessage(ValueError, "shot detection"):
            self.snapshot("face_boxes", smooth=True)
        shots = PluginRunResult.objects.create(plugin_run=self.run, type=PluginRunResult.TYPE_SHOTS, name="shots")
        timeline = Timeline.objects.create(video=self.video, plugin_run_result=shots, name="Shots")
        TimelineSegment.objects.create(timeline=timeline, start=0, end=1.25)
        TimelineSegment.objects.create(timeline=timeline, start=1.25, end=10)
        record = self.snapshot("face_boxes", smooth=True)["layers"][0]["records"][0]
        self.assertAlmostEqual(record["end"], 0.15)

    def test_confidence_and_foreign_cluster_selection(self):
        self.geometry()
        with self.assertRaisesMessage(ValueError, "No detections"):
            self.snapshot("face_boxes", min_confidence=0.95)
        with self.assertRaisesMessage(ValueError, "clusters from this run"):
            self.snapshot(cluster_ids=["not-a-cluster"])

    def test_long_place_sample_remains_visible_when_clip_starts_mid_sample(self):
        self.run.type = "place_clustering"
        self.run.save()
        self.item.time, self.item.delta_time = 0, 5
        self.item.save()
        self.assertEqual(self.snapshot()["layers"][0]["records"][0]["end"], 3.9)

    @patch("backend.management.commands.recover_face_geometry.TaskAnalyserClient")
    def test_recovery_rejects_mismatches_and_preserves_curated_clusters(self, client):
        with self.manager.create_data("BboxesData") as boxes:
            boxes.bboxes.append(BboxData(ref_id="wrong-face", time=1, x=0, y=0, w=0.2, h=0.2))
        client.return_value.download_data.return_value = self.manager.load(boxes.id)
        with self.assertRaisesMessage(CommandError, "original face IDs"):
            call_command("recover_face_geometry", str(self.run.id), "--bbox-data-id", boxes.id)
        self.assertFalse(self.run.results.filter(type=PluginRunResult.TYPE_BBOXES).exists())
        with self.manager.create_data("BboxesData") as matching:
            matching.bboxes.append(BboxData(ref_id="face-1", time=1, x=0, y=0, w=0.2, h=0.2))
        client.return_value.download_data.return_value = self.manager.load(matching.id)
        call_command("recover_face_geometry", str(self.run.id), "--bbox-data-id", matching.id)
        self.assertEqual(self.cluster.items.get().id, self.item.id)
        self.assertEqual(self.cluster.name, "Edited person")
        with self.assertRaisesMessage(CommandError, "already has geometry"):
            call_command("recover_face_geometry", str(self.run.id), "--bbox-data-id", matching.id)

    def test_ocr_expressions_and_pose_preserve_individual_records(self):
        for name, extra in [("ocr_boxes", {"label": "Hello <world>"}),
                            ("face_expressions", {"scores": {"happy": 0.8, "sad": 0.2}}),
                            ("body_pose", {"keypoints": [[0.3, 0.3, 1]] * 17})]:
            result = save_spatial_result(self.run, name, [
                {"time": 1, "delta_time": 0.5, "bbox": [x, 0.2, 0.1, 0.2], **extra} for x in (0.1, 0.7)
            ])
            snapshot = self.snapshot(name, source_id=f"spatial:{result.id.hex}")
            records = snapshot["layers"][0]["records"]
            self.assertEqual(len(records), 2)
            self.assertNotEqual(records[0]["track_id"], records[1]["track_id"])
            if name == "face_expressions":
                self.assertEqual(records[0]["label"], "Predicted happy: 0.80")

    def test_speaker_timestamps_and_scores_are_aligned(self):
        with self.manager.create_data("AnnotationData") as data:
            data.annotations.append(Annotation(start=1, end=2, labels=[{
                "times": [1.1, 1.14], "bbox": [[0.1, 0.2, 0.3, 0.5]] * 2,
                "track_id": "speaker-1", "smoothed_scores": [-1, 2], "fps": 25,
            }]))
        result = PluginRunResult.objects.create(plugin_run=self.run, name="speaker_tracks", type=PluginRunResult.TYPE_SPATIAL, data_id=data.id)
        records = self.snapshot("speaker_tracks", source_id=f"spatial:{result.id.hex}")["layers"][0]["records"]
        self.assertEqual([record["label"] for record in records], ["Not speaking", "Speaking"])
        self.assertAlmostEqual(records[1]["time"], 0.04)
        self.assertAlmostEqual(records[0]["bbox"][2], 0.2)

    def test_ocr_text_references_survive_serialization_and_backend_storage(self):
        with self.manager.create_data("BboxesData") as boxes:
            box = BboxData(time=1, delta_time=0.5, x=0.1, y=0.2, w=0.3, h=0.1)
            boxes.bboxes.append(box)
        with self.manager.create_data("StringsData") as strings:
            strings.strings.append(StringData(ref_id=box.id, text="Recognized text"))
        with self.manager.create_data("AnnotationData") as annotations:
            annotations.annotations.append(Annotation(start=1, end=1.5, labels=["Recognized text"]))
        task = OCRTask()
        task.config["output_path"] = self.directory.name
        downloads = {name: self.manager.load(data.id) for name, data in [("bboxes", boxes), ("strings", strings), ("annotations", annotations)]}
        with patch.object(task, "upload_video", return_value="video"), patch.object(task, "run_analyser", return_value=({}, downloads)):
            task({"fps": 2, "timeline": "OCR"}, video=self.video, user=self.user, plugin_run=self.run)
        result = self.run.results.get(name="ocr_boxes")
        snapshot = self.snapshot("ocr_boxes", source_id=f"spatial:{result.id.hex}")
        self.assertEqual(snapshot["layers"][0]["records"][0]["label"], "Recognized text")
        self.assertEqual(Timeline.objects.get(name="OCR").plugin_run_result_id, result.id)


class SpatialTimingTests(SimpleTestCase):
    def test_tracks_end_at_cuts_gaps_and_ambiguous_crossings(self):
        box = [0.2, 0.2, 0.2, 0.2]
        records = [{"time": timestamp, "bbox": box} for timestamp in (0, 0.5, 1, 3)]
        tracks = assign_tracks(records, cuts=[1])
        self.assertEqual(tracks[0]["track_id"], tracks[1]["track_id"])
        self.assertNotEqual(tracks[1]["track_id"], tracks[2]["track_id"])
        self.assertNotEqual(tracks[2]["track_id"], tracks[3]["track_id"])
        crossed = assign_tracks([records[0], {"time": 0.5, "bbox": box}, {"time": 0.5, "bbox": box}])
        self.assertEqual(len({record["track_id"] for record in crossed}), 3)

    def test_smoothing_interpolates_without_extending_gaps(self):
        layer = {"mode": "face_boxes", "color": "#FFFFFF", "smooth": True, "records": [
            {"time": 0, "end": 0.5, "bbox": [0, 0, 0.2, 0.2], "track_id": "1"},
            {"time": 0.5, "end": 1, "bbox": [0.2, 0, 0.2, 0.2], "track_id": "1"},
        ]}
        scene = OverlayScene({"font_scale": 1, "layers": [layer]}, 320, 180)
        tracks = scene.layers[0][1]
        self.assertAlmostEqual(scene.current_records(layer, tracks, 0.25)[0]["bbox"][0], 0.1)
        self.assertEqual(scene.current_records(layer, tracks, 1), [])

    def test_speaker_identity_needs_unique_spatial_match(self):
        face = {"time": 0, "end": 0.5, "bbox": [0.1, 0.1, 0.2, 0.2], "identity": "a", "label": "A"}
        speaker = {"time": 0.2, "end": 0.24, "bbox": face["bbox"], "label": "Speaking"}
        layers = [{"mode": "face_boxes", "records": [face]}, {"mode": "speaker_tracks", "records": [dict(speaker)]}]
        link_speaker_clusters(layers)
        self.assertEqual(layers[1]["records"][0]["identity"], "a")
        layers[0]["records"].append({**face, "identity": "b"})
        layers[1]["records"] = [dict(speaker)]
        link_speaker_clusters(layers)
        self.assertNotIn("identity", layers[1]["records"][0])

    def test_rotation_transforms_normalized_box(self):
        box = display_box([0.1, 0.2, 0.3, 0.4], 90, 180, 320)
        for actual, expected in zip(box, [36, 192, 108, 288]):
            self.assertAlmostEqual(actual, expected)

    def test_real_spatial_render_keeps_audio_and_cancels(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
            snapshot = {"start": 0.5, "render_end": 1.5, "font_scale": 1, "opacity": 0.65, "audio": True, "max_dimension": 720,
                        "layers": [{"mode": "face_boxes", "label": "Faces", "color": "#FF0000", "show_labels": False,
                                    "records": [{"time": 0, "end": 0.5, "bbox": [0.1, 0.1, 0.4, 0.5]}]}]}
            output = render_video(source, snapshot, root / "render")
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))
            self.assertEqual([stream["codec_type"] for stream in probe["streams"]], ["video", "audio"])
            for timestamp, present in [(0.2, True), (0.8, False)]:
                pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
                red = sum(r > 140 and g < 100 for r, g in zip(pixels[0::3], pixels[1::3]))
                self.assertEqual(red > 50, present)
            calls = iter([False, True])
            with self.assertRaises(RenderCancelled):
                render_video(source, snapshot, root / "cancel", cancelled=lambda: next(calls, True))
            self.assertFalse((root / "cancel/output.partial.mp4").exists())
