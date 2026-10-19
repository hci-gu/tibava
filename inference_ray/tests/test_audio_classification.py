import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tibava_data import AnnotationData, AudioData, Shot, ShotsData
from inference_ray.plugins.audio_classification import AudioClassification


class AudioClassificationTests(unittest.TestCase):
    def inputs(self):
        audio = AudioData()
        audio.open_audio = Mock(return_value=io.BytesIO())
        return {"audio": audio, "shots": ShotsData(shots=[Shot(start=0, end=1), Shot(start=1, end=2)])}

    def test_temporary_clips_are_cleaned_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                plugin = AudioClassification()
                plugin.preload = Mock()
                paths = []

                def split(audio, segments, output_dir):
                    path = Path(output_dir) / "clip.wav"
                    path.write_bytes(b"test")
                    paths.append(path)
                    return [path, path]

                plugin.split_audio_fd_with_librosa = split
                plugin.classify_segment = Mock(side_effect=RuntimeError("inference failed") if fail else [["Speech", "Music"], []])
                manager = Mock()
                manager.create_data.return_value = AnnotationData()
                if fail:
                    with self.assertRaises(RuntimeError):
                        plugin.call(self.inputs(), manager)
                else:
                    result = plugin.call(self.inputs(), manager)
                    self.assertEqual([a.labels for a in result["annotations"].annotations], [["Speech", "Music"], []])
                self.assertTrue(paths)
                self.assertFalse(paths[0].parent.exists())

    def test_split_failure_also_removes_temporary_directory(self):
        plugin = AudioClassification()
        plugin.preload = Mock()
        paths = []

        def split(audio, segments, output_dir):
            path = Path(output_dir) / "partial.wav"
            path.write_bytes(b"partial")
            paths.append(path)
            raise OSError("write failed")

        plugin.split_audio_fd_with_librosa = split
        manager = Mock()
        manager.create_data.return_value = AnnotationData()
        with self.assertRaises(OSError):
            plugin.call(self.inputs(), manager)
        self.assertFalse(paths[0].parent.exists())

    def test_model_load_failure_propagates_and_can_retry(self):
        plugin = AudioClassification()
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
        loader = Mock(side_effect=RuntimeError("model unavailable"))
        transformers = SimpleNamespace(AutoModel=SimpleNamespace(from_pretrained=loader))
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            with self.assertRaises(RuntimeError):
                plugin.preload()
            self.assertIsNone(plugin.model)
            model = Mock()
            model.eval.return_value = model
            model.to.return_value = model
            loader.side_effect = None
            loader.return_value = model
            plugin.preload()
            plugin.preload()
        self.assertEqual(loader.call_count, 2)
        model.to.assert_called_once_with("cuda:0")

    def test_invalid_shots_fail_before_model_loading(self):
        plugin = AudioClassification()
        plugin.preload = Mock()
        manager = Mock()
        manager.create_data.return_value = AnnotationData()
        inputs = self.inputs()
        inputs["shots"].shots = [Shot(start=2, end=1)]
        with self.assertRaises(ValueError):
            plugin.call(inputs, manager)
        plugin.preload.assert_not_called()

    def test_fixed_window_and_threshold_use_upstream_semantics(self):
        import torch

        plugin = AudioClassification()
        plugin.device = "cpu"
        logits = torch.full((1, 527), -10.0)
        logits[0, 0], logits[0, 137] = 2.0, 1.0
        plugin.model = Mock(return_value=logits)
        for frames in (500, 1200):
            with self.subTest(frames=frames):
                bank = torch.ones((frames, 128))
                torchaudio = SimpleNamespace(compliance=SimpleNamespace(kaldi=SimpleNamespace(fbank=Mock(return_value=bank))))
                with patch.dict(sys.modules, {"torchaudio": torchaudio}), patch(
                    "soundfile.read", return_value=([0.0] * 16000, 16000)
                ):
                    labels = plugin.classify_segment("clip.wav", {"top_k": 3, "threshold": 0.5})
                    self.assertEqual(labels, ["Speech", "Music"])
                    self.assertEqual(tuple(plugin.model.call_args.args[0].shape), (1, 1, 1024, 128))
                    self.assertEqual(plugin.classify_segment("clip.wav", {"top_k": 3, "threshold": 1.0}), [])


if __name__ == "__main__":
    unittest.main()
