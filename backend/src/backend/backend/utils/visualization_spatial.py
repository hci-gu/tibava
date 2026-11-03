"""Adapters for spatial results and the user's current cluster assignments."""

import base64
import hashlib
import io
import math
import re
from bisect import bisect_right
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from PIL import Image

from backend.models import ClusterTimelineItem, PluginRun, PluginRunResult, Timeline, VideoAnalysisState
from utils.spatial_tracking import assign_tracks, box_iou, valid_box


SPATIAL_NAMES = {"ocr_boxes": "OCR boxes", "speaker_tracks": "Active speaker",
                 "face_expressions": "Per-face expression scores", "body_pose": "Body pose and people"}
PALETTE = ["#64D8FF", "#FFE082", "#FF8A80", "#B9F6CA", "#EA80FC", "#FFAB91", "#80CBC4", "#B388FF"]


def cluster_color(cluster_id):
    return PALETTE[int(hashlib.sha256(str(cluster_id).encode()).hexdigest()[:8], 16) % len(PALETTE)]


def spatial_sources(video):
    sources = []
    runs = PluginRun.objects.filter(video=video, status=PluginRun.STATUS_DONE,
                                   type__in=["face_clustering", "place_clustering"]).prefetch_related("results")
    for run in runs:
        clusters = list(ClusterTimelineItem.objects.filter(plugin_run=run, video=video).order_by("name", "id"))
        if not clusters:
            continue
        results = list(run.results.all())
        face = run.type == "face_clustering"
        result_types = {result.type for result in results}
        geometry = face and {PluginRunResult.TYPE_BBOXES, PluginRunResult.TYPE_IMAGE_EMBEDDINGS}.issubset(result_types)
        sources.append({
            "id": f"clusters:{run.id.hex}", "name": "Face clusters" if face else "Place clusters",
            "kind": "face_clusters" if face else "place_clusters", "run_id": run.id.hex,
            "plugin": run.type, "date": run.date.isoformat(),
            "modes": ["cluster_cards", "face_boxes"] if geometry else ["cluster_cards"],
            "landmarks_available": any(result.type == PluginRunResult.TYPE_KEYPOINTS for result in results),
            "notice": "" if geometry or not face else "This older run has no saved boxes. Cards work now; run Face clustering again for boxes and landmarks.",
            "clusters": [{"id": cluster.id.hex, "name": cluster.name, "color": cluster_color(cluster.id)} for cluster in clusters],
        })
    for result in PluginRunResult.objects.filter(plugin_run__video=video, plugin_run__status=PluginRun.STATUS_DONE,
                                                type=PluginRunResult.TYPE_SPATIAL).select_related("plugin_run"):
        if result.name not in SPATIAL_NAMES:
            continue
        sources.append({"id": f"spatial:{result.id.hex}", "name": SPATIAL_NAMES[result.name],
                        "kind": result.name, "modes": [result.name], "run_id": result.plugin_run_id.hex,
                        "plugin": result.plugin_run.type, "date": result.plugin_run.date.isoformat()})
    return sources


def normalize_spatial_options(layer, source):
    result = {}
    clusters = source.get("clusters", [])
    if clusters:
        allowed = {entry["id"] for entry in clusters}
        selected = layer.get("cluster_ids", [entry["id"] for entry in clusters])
        if not isinstance(selected, list) or not 1 <= len(selected) <= 12 or any(not isinstance(value, str) or value not in allowed for value in selected):
            raise ValueError("Choose between one and twelve clusters from this run.")
        if len(set(selected)) != len(selected):
            raise ValueError("A cluster is selected twice.")
        result["cluster_ids"] = selected
        colors = layer.get("cluster_colors", {})
        if not isinstance(colors, dict) or any(key not in allowed or not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color) for key, color in colors.items()):
            raise ValueError("Invalid cluster colors.")
        result["cluster_colors"] = colors
    for name, default in (("show_boxes", True), ("show_labels", True), ("show_landmarks", False),
                          ("show_legend", True), ("smooth", False), ("show_skeleton", True)):
        value = layer.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"Invalid {name} option.")
        result[name] = value
    if result["show_landmarks"] and not source.get("landmarks_available", False):
        raise ValueError("This run has no saved facial landmarks.")
    try:
        confidence = float(layer.get("min_confidence", 0.6))
    except (ValueError, TypeError):
        raise ValueError("Invalid detection threshold.") from None
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("Detection threshold must be between 0 and 1.")
    result["min_confidence"] = confidence
    if layer.get("mode", source["modes"][0]) == "cluster_cards":
        result["smooth"] = False
    return result


def shot_cuts(video):
    state = VideoAnalysisState.objects.filter(video=video).first()
    timeline = state.selected_shots if state and state.selected_shots_id else None
    if timeline is None or timeline.video_id != video.id:
        timeline = Timeline.objects.filter(video=video, plugin_run_result__type=PluginRunResult.TYPE_SHOTS).first()
    if timeline is None:
        return None
    segments = list(timeline.timelinesegment_set.values_list("start", "end"))
    return sorted({float(value) for segment in segments for value in segment if math.isfinite(value)}) if segments else None


def thumbnail_snapshot(image_path):
    # Resolve only known thumbnail basenames under prediction storage; never fetch URLs.
    filename = Path(urlparse(image_path or "").path).name
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|jpeg|png|webp)", filename):
        return None
    path = Path(settings.DATA_OUTPUT_PATH) / filename[:2] / filename[2:4] / filename
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((160, 120))
            output = io.BytesIO()
            image.save(output, "PNG")
            return base64.b64encode(output.getvalue()).decode("ascii")
    except (OSError, ValueError):
        return None


def cluster_snapshot(video, layer, manager, start, end):
    run_id = layer["source_id"].split(":", 1)[1]
    run = PluginRun.objects.get(id=run_id, video=video, status=PluginRun.STATUS_DONE)
    clusters = ClusterTimelineItem.objects.filter(video=video, plugin_run=run, id__in=layer["cluster_ids"]).prefetch_related("items")
    memberships, cards, records = {}, [], []
    for cluster in clusters:
        identity = cluster.id.hex
        items = list(cluster.items.all())
        representative = next((item for item in items if item.is_sample and item.image_path), next(iter(items), None))
        color = layer.get("cluster_colors", {}).get(identity, cluster_color(cluster.id))
        cards.append({"id": identity, "label": cluster.name, "color": color,
                      "thumbnail": thumbnail_snapshot(representative.image_path) if representative else None})
        for item in items:
            memberships[item.embedding_id] = {"identity": identity, "label": cluster.name, "color": color}
            if layer["mode"] == "cluster_cards" and item.time <= end and item.time + (item.delta_time or 0.5) > start:
                records.append({"time": item.time, "end": item.time + (item.delta_time or 0.5),
                                **memberships[item.embedding_id]})
    if layer["mode"] == "face_boxes":
        box_result = run.results.filter(type=PluginRunResult.TYPE_BBOXES).first()
        embeddings_result = run.results.filter(type=PluginRunResult.TYPE_IMAGE_EMBEDDINGS).first()
        if box_result is None or embeddings_result is None:
            raise ValueError("This clustering run has no saved geometry. Run Face clustering again.")
        with manager.load(embeddings_result.data_id) as embeddings:
            by_face = {embedding.ref_id: memberships[embedding.id] for embedding in embeddings.embeddings if embedding.id in memberships}
        points = {}
        point_result = run.results.filter(type=PluginRunResult.TYPE_KEYPOINTS).first()
        if layer["show_landmarks"] and point_result:
            with manager.load(point_result.data_id) as landmarks:
                points = {point.ref_id: [[float(x), float(y), 1] for x, y in zip(point.x, point.y)] for point in landmarks.kpss}
        with manager.load(box_result.data_id) as boxes:
            for box in boxes.bboxes:
                if box.ref_id in by_face and box.time <= end + 0.75 and box.time + (box.delta_time or 0.5) > start - 0.75 and box.det_score >= layer["min_confidence"]:
                    records.append({"time": box.time, "end": box.time + (box.delta_time or 0.5),
                                    "bbox": [box.x, box.y, box.w, box.h], "confidence": box.det_score,
                                    "keypoints": points.get(box.ref_id, []), "face_id": box.ref_id, **by_face[box.ref_id]})
    return {**layer, "cards": cards, "records": records, "run_id": run.id.hex}


def result_snapshot(video, layer, manager, start, end):
    result = PluginRunResult.objects.get(id=layer["source_id"].split(":", 1)[1], plugin_run__video=video,
                                        plugin_run__status=PluginRun.STATUS_DONE, type=PluginRunResult.TYPE_SPATIAL)
    records = []
    with manager.load(result.data_id) as data:
        for annotation in data.annotations:
            if annotation.end < start - 1 or annotation.start > end + 1:
                continue
            for entry in annotation.labels:
                if result.name == "speaker_tracks":
                    times = entry.get("times") or [frame / entry.get("fps", 25) for frame in entry["frames"]]
                    for timestamp, box, score in zip(times, entry["bbox"], entry["smoothed_scores"]):
                        if start - 1 <= timestamp <= end + 1:
                            records.append({"time": timestamp, "end": timestamp + 1 / entry.get("fps", 25),
                                            "bbox": [box[0], box[1], box[2] - box[0], box[3] - box[1]],
                                            "track_id": entry["track_id"], "speaker_score": float(score),
                                            "label": "Speaking" if score >= 0 else "Not speaking"})
                else:
                    records.append({**entry, "time": annotation.start, "end": annotation.end})
    return {**layer, "records": records, "data_id": result.data_id, "run_id": result.plugin_run_id.hex}


def build_spatial_snapshot(video, layer, manager, start, end):
    snapshot = cluster_snapshot(video, layer, manager, start, end) if layer["source_id"].startswith("clusters:") else result_snapshot(video, layer, manager, start, end)
    records = snapshot["records"]
    cuts = shot_cuts(video)
    if layer["smooth"] and cuts is None:
        raise ValueError("Run shot detection before enabling smooth boxes, so interpolation stops at cuts.")
    filtered = []
    for record in records:
        if not all(math.isfinite(float(record[key])) for key in ("time", "end")) or record["end"] <= record["time"]:
            continue
        if "bbox" in record and not valid_box(record["bbox"]):
            continue
        if record.get("confidence", 1) < layer["min_confidence"]:
            continue
        filtered.append(record)
    if layer["mode"] not in ("cluster_cards", "speaker_tracks"):
        filtered = assign_tracks(filtered, cuts=cuts or [], max_gap=0.75)
    for record in filtered:
        # A held last sample must not bleed into the following shot.
        next_index = bisect_right(cuts or [], record["time"])
        if cuts and next_index < len(cuts):
            record["end"] = min(record["end"], cuts[next_index])
        record["time"] -= start
        record["end"] -= start
        if layer["mode"] == "body_pose":
            record["label"] = f"Person {record.get('track_id', '').removeprefix('track-')}"
        elif layer["mode"] == "face_expressions" and record.get("scores"):
            name, score = max(record["scores"].items(), key=lambda pair: pair[1])
            record["label"] = f"Predicted {name}: {score:.2f}"
    if not any(record["time"] < end - start and record["end"] > 0 for record in filtered):
        raise ValueError(f"No detections in this clip for {layer['label']} at the selected threshold.")
    snapshot["records"] = filtered
    snapshot["coordinate_space"] = "normalized_decoded_xywh"
    return snapshot


def link_speaker_clusters(layers):
    """Optional unambiguous spatial join across runs; never infer from time alone."""
    faces = sorted([record for layer in layers if layer["mode"] == "face_boxes" for record in layer.get("records", [])], key=lambda record: record["time"])
    times = [face["time"] for face in faces]
    max_hold = max((face["end"] - face["time"] for face in faces), default=0)
    for layer in layers:
        if layer["mode"] != "speaker_tracks":
            continue
        for record in layer["records"]:
            nearby = faces[bisect_right(times, record["time"] - max_hold):bisect_right(times, record["time"])]
            candidates = sorted([(box_iou(record["bbox"], face["bbox"]), face) for face in nearby
                                 if face["time"] <= record["time"] < face["end"]], key=lambda pair: pair[0], reverse=True)
            if candidates and candidates[0][0] >= 0.6 and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] > 0.1):
                face = candidates[0][1]
                record["identity"] = face["identity"]
                record["label"] = f"{face['label']} · {record['label']}"
