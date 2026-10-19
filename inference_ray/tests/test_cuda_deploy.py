import importlib.util
from pathlib import Path
import unittest
import yaml


spec = importlib.util.spec_from_file_location("prepare_cuda_deploy", Path(__file__).resolve().parents[1] / "prepare-cuda-deploy.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CudaRuntimeIsolationTests(unittest.TestCase):
    def test_deployment_files_keep_eat_isolated_and_upstream_apps_present(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("deploy.yml", "deploy.cuda.yml"):
            with self.subTest(filename=filename):
                config = yaml.safe_load((root / filename).read_text())
                apps = {application["name"]: application for application in config["applications"]}
                self.assertEqual(len(apps), len(config["applications"]))
                eat = apps["audio_classification"]["deployments"][0]["ray_actor_options"]["runtime_env"]
                self.assertIn("torch==2.6.0", eat["pip"])
                self.assertIn("torchaudio==2.6.0", eat["pip"])
                self.assertIn("timm==0.9.2", eat["pip"])
                self.assertNotIn("torch==2.11.0", eat["pip"])
                for name in ("movie_pattern_intensify", "movie_pattern_shot_reverse_shot", "movie_pattern_opposition", "movie_pattern_frameshare", "vllm_ollama"):
                    self.assertEqual(apps[name]["args"]["model"], name)

    def test_eat_does_not_inherit_torch_211_library_override(self):
        runtime = {"pip": ["torch==2.6.0", "torchaudio==2.6.0", "timm==0.9.2"]}
        prepare.prepare_runtime_env(runtime)
        self.assertNotIn("LD_LIBRARY_PATH", runtime.get("env_vars", {}))

    def test_image_torch_keeps_its_cuda_libraries(self):
        runtime = {"pip": ["torch==2.11.0"]}
        prepare.prepare_runtime_env(runtime)
        self.assertEqual(runtime["env_vars"]["LD_LIBRARY_PATH"], prepare.TORCH_CUDA_LIBRARIES)

    def test_onnx_keeps_image_cuda12_path(self):
        runtime = {"pip": ["torch==2.11.0", "onnxruntime-gpu==1.21.1"]}
        prepare.prepare_runtime_env(runtime)
        self.assertNotIn("LD_LIBRARY_PATH", runtime.get("env_vars", {}))
