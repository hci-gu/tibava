import unittest

import numpy as np

from inference_ray.plugins.shot_annotation import ShotAnnotator


class ScalarProbabilities:
    def __init__(self, time, y):
        self.time = np.asarray(time)
        self.y = np.asarray(y)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class ShotAnnotationBoundaryTests(unittest.TestCase):
    def test_shot_intervals_include_start_and_exclude_end(self):
        probabilities = [
            ("label", ScalarProbabilities(time=[0.0, 1.0], y=[0.25, 0.75]))
        ]

        first = ShotAnnotator.mean_shot_probabilities(
            None, start=0.0, end=1.0, probs=probabilities
        )
        second = ShotAnnotator.mean_shot_probabilities(
            None, start=1.0, end=2.0, probs=probabilities
        )

        self.assertEqual(first, {"label": 0.25})
        self.assertEqual(second, {"label": 0.75})


if __name__ == "__main__":
    unittest.main()
