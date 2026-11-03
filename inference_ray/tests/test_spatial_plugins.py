from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from data import BboxData, DataManager, Shot
from inference_ray.plugins.face_tracking import FaceTracker
from inference_ray.plugins.body_pose import BodyPose


class SpatialPluginTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = DataManager(self.directory.name)

    def track(self, times, min_track=0.1):
        with self.manager.create_data("BboxesData") as boxes:
            boxes.bboxes = [BboxData(time=time, delta_time=0.04, x=0.2, y=0.2, w=0.2, h=0.2, det_score=0.9) for time in times]
        with self.manager.create_data("ShotsData") as shots:
            shots.shots = [Shot(start=2, end=4)]
        with self.manager.create_data("FacesData") as faces:
            pass
        result = FaceTracker()({"bboxes": boxes, "shots": shots, "faces": faces}, self.manager,
                               parameters={"fps": 25, "min_track": min_track, "num_failed_det": 10, "min_face_size": 0.05})
        with self.manager.load(result["track_data"].id) as data:
            return data.annotations

    def test_tracker_preserves_empty_frame_offsets_and_last_frame(self):
        track = self.track([2, 2.04, 2.16, 2.20])[0]
        self.assertEqual(track.labels[0]["frames"], [50, 51, 52, 53, 54, 55])
        self.assertAlmostEqual(track.end, 2.24)
        self.assertEqual(len(track.labels[0]["bboxes"]), 6)

    def test_tracker_uses_seconds_and_handles_empty_input(self):
        self.assertEqual(self.track([2, 2.04, 2.08], min_track=1), [])
        self.assertEqual(self.track([]), [])

    def test_body_pose_stores_normalized_individual_people(self):
        import torch
        frame = {"time": 1.5, "frame": np.zeros((100, 200, 3), dtype=np.uint8)}
        class Decoder:
            def __init__(self, **kwargs):
                pass
            def __iter__(self):
                return iter([frame])
            def duration(self):
                return 2
        prediction = {"scores": torch.tensor([0.9, 0.1]), "boxes": torch.tensor([[20, 10, 100, 90]] * 2),
                      "keypoints": torch.tensor([[[40, 30, 1]] * 17] * 2), "keypoints_scores": torch.tensor([[3.] * 17] * 2)}
        plugin = BodyPose({"model_device": "cpu"})
        plugin.model = lambda images: [prediction]
        with self.manager.create_data("VideoData") as video:
            video.ext = "mp4"
            with video.open_video("w") as handle:
                handle.write(b"unused by mocked decoder")
        with patch.object(plugin, "load_model"), patch("inference_ray.plugins.body_pose.VideoDecoder", Decoder):
            result = plugin({"video": self.manager.load(video.id)}, self.manager, parameters={"fps": 2, "threshold": 0.8})
        with self.manager.load(result["poses"].id) as poses:
            self.assertEqual(len(poses.annotations), 1)
            self.assertEqual(poses.annotations[0].labels[0]["bbox"], [0.1, 0.1, 0.4, 0.8])
            self.assertEqual(poses.annotations[0].labels[0]["keypoints"][0], [0.2, 0.3, 1])
            self.assertEqual(poses.annotations[0].end, 2)

    def test_body_pose_missing_model_gives_actionable_error(self):
        plugin = BodyPose({"model_file": str(Path(self.directory.name) / "missing.pth")})
        with self.assertRaisesRegex(ValueError, "download-body-pose-model"):
            plugin.load_model()


if __name__ == "__main__":
    unittest.main()
