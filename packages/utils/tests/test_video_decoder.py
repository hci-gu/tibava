import tempfile
import unittest
from pathlib import Path

import av
import numpy as np

from utils import VideoDecoder, VideoBatcher


class VideoDecoderTests(unittest.TestCase):
    def test_filter_flush_dimensions_file_handle_and_partial_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            with av.open(str(path), mode="w") as container:
                stream = container.add_stream("mpeg4", rate=10)
                stream.width, stream.height = 64, 48
                stream.pix_fmt = "yuv420p"
                for index in range(10):
                    frame = av.VideoFrame.from_ndarray(
                        np.full((48, 64, 3), index * 20, dtype=np.uint8), format="rgb24"
                    )
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            with path.open("rb") as source:
                decoder = VideoDecoder(source, fps=5, max_dimension=32)
                frames = list(decoder)
                self.assertEqual(len(frames), 5)
                self.assertEqual(frames[0]["frame"].shape, (24, 32, 3))
                self.assertAlmostEqual(frames[-1]["time"], 0.8)
            batches = list(VideoBatcher(VideoDecoder(str(path)), batch_size=8))
            self.assertEqual([len(batch["frame"]) for batch in batches], [8, 2])


if __name__ == "__main__":
    unittest.main()
