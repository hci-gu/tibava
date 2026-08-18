#!/usr/bin/env python3
import sys
from pathlib import Path

import yaml


TORCH_CUDA_LIBRARIES = ":".join(
    [
        "/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib",
        "/app/.venv/lib/python3.12/site-packages/nvidia/cu13/lib",
        "/app/.venv/lib/python3.12/site-packages/nvidia/nccl/lib",
        "/app/.venv/lib/python3.12/site-packages/nvidia/nvshmem/lib",
        "/app/.venv/lib/python3.12/site-packages/nvidia/cusparselt/lib",
        "/usr/local/nvidia/lib",
        "/usr/local/nvidia/lib64",
    ]
)


def package_name(spec: str) -> str:
    return spec.split("==", 1)[0].split(">=", 1)[0].split("<", 1)[0].lower()


def runtime_uses_torch(runtime_env: dict) -> bool:
    packages = runtime_env.get("pip", []) + runtime_env.get("uv", [])
    names = {package_name(str(spec)) for spec in packages}
    return any(
        name == "torch" or name.startswith("torchvision") or name == "open-clip-torch"
        for name in names
    )


def prepare_runtime_env(runtime_env: dict) -> None:
    packages = runtime_env.get("pip", []) + runtime_env.get("uv", [])
    names = {package_name(str(spec)) for spec in packages}
    uses_torch = runtime_uses_torch(runtime_env)
    uses_onnx_gpu = "onnxruntime-gpu" in names

    # ONNX Runtime GPU uses the image's CUDA 12 libraries. Torch 2.11 uses
    # its CUDA 13 libraries and must not inherit the image's cuDNN 9.1 path.
    if uses_torch and not uses_onnx_gpu:
        runtime_env.setdefault("env_vars", {})[
            "LD_LIBRARY_PATH"
        ] = TORCH_CUDA_LIBRARIES


def main(source_path: Path, output_path: Path) -> None:
    with source_path.open(encoding="utf-8") as source:
        config = yaml.safe_load(source)

    for application in config["applications"]:
        app_runtime_env = application.get("runtime_env")
        if app_runtime_env:
            prepare_runtime_env(app_runtime_env)

        for deployment in application.get("deployments", []):
            actor_options = deployment.setdefault("ray_actor_options", {})
            runtime_env = actor_options.get("runtime_env")
            if runtime_env:
                prepare_runtime_env(runtime_env)

            effective_runtime_env = runtime_env or app_runtime_env or {}
            if runtime_uses_torch(effective_runtime_env):
                # Without a Ray GPU reservation CUDA_VISIBLE_DEVICES is empty and
                # Torch silently runs heavyweight inference on the CPU.
                actor_options.setdefault("num_gpus", 0.05)

            autoscaling = deployment.get("autoscaling_config")
            if autoscaling and autoscaling.get("min_replicas", 0) == 0:
                autoscaling["downscale_delay_s"] = 5
                autoscaling["upscale_delay_s"] = 0
                autoscaling["max_replicas"] = min(
                    autoscaling.get("max_replicas", 4),
                    1 if actor_options.get("num_gpus", 0) else 4,
                )

    output_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare-cuda-deploy.py SOURCE OUTPUT")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
