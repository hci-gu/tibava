"""Resolve authorized stored results into a reproducible visualization snapshot."""

import math
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Q

from backend.models import PluginRun, PluginRunResult, Timeline
from backend.utils.visualization_render import scalar_intervals
from backend.utils.visualization_spatial import spatial_sources, normalize_spatial_options, build_spatial_snapshot, link_speaker_clusters
from data import DataManager


def export_directory(job_id):
    return Path(settings.DATA_CACHE_ROOT) / "visualization_exports" / uuid.UUID(str(job_id)).hex


def remove_export_files(job_id):
    directory = export_directory(job_id)
    # Only these generated files are owned by an export. Never follow subdirectories.
    for filename in ("snapshot.json", "overlay.ass", "progress.txt", "ffmpeg.log", "output.partial.mp4", "output.mp4"):
        (directory / filename).unlink(missing_ok=True)
    if directory.exists():
        try:
            directory.rmdir()
        except OSError:
            pass


def available_sources(video):
    entries = []
    results = PluginRunResult.objects.filter(
        plugin_run__video=video, plugin_run__status=PluginRun.STATUS_DONE,
        type=PluginRunResult.TYPE_SCALAR,
    ).select_related("plugin_run").prefetch_related("timeline_set__parent").order_by("plugin_run__date", "name", "id")
    for result in results:
        timelines = list(result.timeline_set.all())
        names = [f"{timeline.parent.name} / {timeline.name}" if timeline.parent else timeline.name for timeline in timelines]
        entries.append({
            "id": f"result:{result.id.hex}", "name": " · ".join(names) or result.name,
            "kind": "scalar", "run_id": result.plugin_run_id.hex,
            "plugin": result.plugin_run.type, "date": result.plugin_run.date.isoformat(),
        })
    timelines = Timeline.objects.filter(video=video).filter(
        Q(type__in=[Timeline.TYPE_ANNOTATION, Timeline.TYPE_TRANSCRIPT]) |
        Q(plugin_run_result__type=PluginRunResult.TYPE_SHOTS)
    ).filter(
        Q(plugin_run_result__isnull=True) | Q(plugin_run_result__plugin_run__status=PluginRun.STATUS_DONE)
    ).select_related("plugin_run_result__plugin_run", "parent").annotate(segment_count=Count("timelinesegment"))
    for timeline in timelines:
        if not timeline.segment_count:
            continue
        result = timeline.plugin_run_result
        entries.append({
            "id": f"timeline:{timeline.id.hex}",
            "name": f"{timeline.parent.name} / {timeline.name}" if timeline.parent else timeline.name,
            "kind": "caption" if timeline.type == Timeline.TYPE_TRANSCRIPT else "label",
            "run_id": result.plugin_run_id.hex if result else None,
            "plugin": result.plugin_run.type if result else None,
            "date": result.plugin_run.date.isoformat() if result else None,
        })
    return entries + spatial_sources(video)


def finite_number(value, name, minimum, maximum):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {name}.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return number


def validate_configuration(video, payload, sources):
    if not isinstance(payload, dict):
        raise ValueError("Expected export settings.")
    duration = float(video.duration or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The video has no valid duration.")
    start = finite_number(payload.get("start", 0), "Start time", 0, duration)
    end = finite_number(payload.get("end", duration), "End time", 0, duration)
    if end - start < 0.1:
        raise ValueError("Select a clip at least 0.1 seconds long.")
    layers = payload.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 6:
        raise ValueError("Select between one and six visualization layers.")
    by_id = {entry["id"]: entry for entry in sources}
    normalized = []
    used = set()
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("Invalid visualization layer.")
        source_id = layer.get("source_id")
        if not isinstance(source_id, str) or source_id not in by_id or source_id in used:
            raise ValueError("A selected result is unavailable or selected twice.")
        used.add(source_id)
        entry = by_id[source_id]
        modes = entry.get("modes", ["scalar"] if entry["kind"] == "scalar" else ["label", "caption"])
        mode = layer.get("mode", modes[0] if entry.get("modes") else entry["kind"])
        if mode not in modes:
            raise ValueError("Unsupported visualization for this result.")
        color = layer.get("color", "#FFFFFF")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("Choose a valid text color.")
        label = layer.get("label", entry["name"])
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("Layer names must contain between 1 and 80 characters.")
        options = normalize_spatial_options(layer, entry) if entry.get("modes") else {}
        normalized.append({"source_id": source_id, "mode": mode, "color": color, "label": label.strip(), **options})
    if sum(layer["mode"] == "caption" for layer in normalized) > 1:
        raise ValueError("Select one caption timeline at a time.")
    if sum(layer["mode"] in ("scalar", "label") for layer in normalized) > 5:
        raise ValueError("Select at most five panel rows.")
    if sum(len(layer.get("cluster_ids", [])) for layer in normalized) > 12:
        raise ValueError("Choose at most twelve clusters across all layers.")
    dimension = payload.get("max_dimension", 1280)
    if dimension not in [720, 1280, 1920]:
        raise ValueError("Choose a supported output resolution.")
    audio = payload.get("audio", True)
    if not isinstance(audio, bool):
        raise ValueError("Invalid audio option.")
    return {
        "start": start, "end": end, "layers": normalized, "audio": audio,
        "max_dimension": dimension,
        "font_scale": finite_number(payload.get("font_scale", 1), "Text size", 0.8, 1.3),
        "opacity": finite_number(payload.get("opacity", 0.65), "Panel opacity", 0.2, 0.9),
    }


def build_snapshot(video, configuration, preview):
    start = configuration["start"]
    end = min(configuration["end"], start + 5) if preview else configuration["end"]
    snapshot = {**configuration, "version": 2, "video_id": video.id.hex,
                "source_file": video.file.hex, "source_ext": video.ext,
                "render_end": end, "layers": []}
    manager = DataManager(settings.DATA_OUTPUT_PATH)
    for layer in configuration["layers"]:
        kind, source_id = layer["source_id"].split(":", 1)
        if kind in ("clusters", "spatial"):
            snapshot["layers"].append(build_spatial_snapshot(video, layer, manager, start, end))
            continue
        if kind == "result":
            result = PluginRunResult.objects.get(
                id=source_id, plugin_run__video=video, plugin_run__status=PluginRun.STATUS_DONE,
                type=PluginRunResult.TYPE_SCALAR,
            )
            with manager.load(result.data_id) as data:
                if len(data.time) != len(data.y):
                    raise ValueError("Scalar timestamps and values have different lengths.")
                intervals = scalar_intervals(data.time, data.y, data.delta_time, start, end)
            provenance = {"data_id": result.data_id, "run_id": result.plugin_run_id.hex}
        else:
            timeline = Timeline.objects.get(id=source_id, video=video)
            intervals = []
            segments = timeline.timelinesegment_set.filter(end__gt=start, start__lt=end).prefetch_related("annotations")
            for index, segment in enumerate(segments):
                if not math.isfinite(segment.start) or not math.isfinite(segment.end):
                    continue
                labels = " · ".join(annotation.name for annotation in segment.annotations.all())
                if not labels and layer["mode"] == "caption":
                    continue
                intervals.append([max(start, segment.start) - start, min(end, segment.end) - start,
                                  labels or f"Segment at {segment.start:.2f}s"])
            provenance = {"timeline_id": timeline.id.hex}
        if not intervals:
            raise ValueError(f"No data in the selected clip for {layer['label']}.")
        snapshot["layers"].append({**layer, **provenance, "intervals": intervals})
    link_speaker_clusters(snapshot["layers"])
    return snapshot
