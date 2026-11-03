"""Conservative geometry-only tracking. Coordinates are normalized xywh."""

from bisect import bisect_right
from collections import defaultdict
import math


def box_iou(a, b):
    intersection = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    union = max(0, a[2] * a[3]) + max(0, b[2] * b[3]) - intersection
    return intersection / union if union > 0 else 0.0


def valid_box(box):
    return isinstance(box, (list, tuple)) and len(box) == 4 and all(
        isinstance(v, (float, int)) and math.isfinite(v) for v in box
    ) and box[2] > 0 and box[3] > 0


def assign_tracks(records, cuts=(), max_gap=0.75, min_iou=0.3):
    """Mutual, unambiguous best matches only; cut/gap/identity changes end tracks."""
    frames = defaultdict(list)
    for source in records:
        if valid_box(source.get("bbox")):
            frames[float(source["time"])].append(dict(source))
    cuts = sorted(set(cuts))
    previous = []
    result = []
    counter = 0
    last_time = None
    for timestamp in sorted(frames):
        current = frames[timestamp]
        if last_time is None or timestamp - last_time > max_gap or bisect_right(cuts, last_time) != bisect_right(cuts, timestamp):
            previous = []
        candidates = []
        for i, old in enumerate(previous):
            for j, new in enumerate(current):
                if old.get("identity") != new.get("identity"):
                    continue
                score = box_iou(old["bbox"], new["bbox"])
                if score >= min_iou:
                    candidates.append((score, i, j))
        for score, i, j in sorted(candidates, reverse=True):
            rivals = [value for value, oi, nj in candidates if (oi == i or nj == j) and (oi, nj) != (i, j)]
            if any(value >= score - 0.05 for value in rivals):
                continue
            if "track_id" not in current[j]:
                current[j]["track_id"] = previous[i]["track_id"]
        for record in current:
            if "track_id" not in record:
                counter += 1
                record["track_id"] = f"track-{counter}"
            result.append(record)
        previous, last_time = current, timestamp
    return result
