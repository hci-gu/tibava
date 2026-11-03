from collections import defaultdict
import math

import numpy as np

from inference_ray.plugin import AnalyserPlugin, AnalyserPluginManager
from data import BboxesData, AnnotationData, Annotation, ShotsData, FacesData
from utils.spatial_tracking import assign_tracks


default_parameters = {"fps": 25, "min_track": 1, "num_failed_det": 10, "min_face_size": 0.05}


@AnalyserPluginManager.export("face_tracking")
class FaceTracker(
    AnalyserPlugin,
    config={"data_dir": "/data/"}, parameters=default_parameters, version="0.2",
    requires={"bboxes": BboxesData, "faces": FacesData, "shots": ShotsData},
    provides={"track_data": AnnotationData},
):
    def call(self, inputs, data_manager, parameters=None, callbacks=None):
        with inputs["bboxes"] as boxes, inputs["shots"] as shots, data_manager.create_data("AnnotationData") as output:
            if not boxes.bboxes:
                return {"track_data": output}
            delta = float(boxes.bboxes[0].delta_time or 1 / parameters["fps"])
            if not math.isfinite(delta) or delta <= 0:
                raise ValueError("Face detections require a positive sampling interval")
            fps = 1 / delta
            records = [{"time": float(box.time), "bbox": [box.x, box.y, box.w, box.h], "face_id": box.ref_id}
                       for box in boxes.bboxes if box.det_score >= 0.6 and box.h >= parameters["min_face_size"]]
            # Explicit timestamps preserve empty frames and shot offsets.
            for shot_index, shot in enumerate(shots.shots):
                tracks = defaultdict(list)
                for record in assign_tracks(
                    [record for record in records if shot.start <= record["time"] < shot.end],
                    max_gap=min(0.5, (parameters["num_failed_det"] + 1) / fps), min_iou=0.5,
                ):
                    tracks[record["track_id"]].append(record)
                for track_id, track in tracks.items():
                    if len(track) < 2 or track[-1]["time"] - track[0]["time"] + delta < parameters["min_track"]:
                        continue
                    sampled_frames = np.array([round(record["time"] * fps) for record in track])
                    frames = np.arange(sampled_frames[0], sampled_frames[-1] + 1)
                    xywh = np.array([record["bbox"] for record in track])
                    xyxy = xywh.copy()
                    xyxy[:, 2:] += xyxy[:, :2]
                    interpolated = np.stack([np.interp(frames, sampled_frames, xyxy[:, column]) for column in range(4)], axis=1)
                    output.annotations.append(Annotation(
                        start=float(frames[0] / fps), end=float((frames[-1] + 1) / fps),
                        labels=[{"frames": frames.tolist(), "bboxes": interpolated.tolist(),
                                 "track_id": f"shot-{shot_index}-{track_id}", "fps": fps}],
                    ))
            self.update_callbacks(callbacks, progress=1.0)
            return {"track_data": output}
