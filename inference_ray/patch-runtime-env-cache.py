#!/usr/bin/env python3
import sys
from pathlib import Path


MARKER = "# TIBAVA_PERSISTENT_RUNTIME_ENV_CACHE"


def patch_plugin(path: Path, resource_attribute: str) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return

    needle = f"        try_to_create_directory(self.{resource_attribute})\n"
    if source.count(needle) != 1:
        raise RuntimeError(f"Could not locate Ray cache initialization in {path}")

    replacement = needle + (
        f"        {MARKER}\n"
        f"        for cache_hash in os.listdir(self.{resource_attribute}):\n"
        f"            cache_path = os.path.join(self.{resource_attribute}, cache_hash)\n"
        "            python_path = virtualenv_utils.get_virtualenv_python(cache_path)\n"
        "            if os.path.exists(python_path):\n"
        "                self._created_hash_bytes[cache_hash] = 1\n"
    )
    path.write_text(source.replace(needle, replacement), encoding="utf-8")


def main() -> None:
    site_packages = Path(sys.prefix) / "lib" / "python3.12" / "site-packages"
    runtime_env = site_packages / "ray" / "_private" / "runtime_env"
    patch_plugin(runtime_env / "pip.py", "_pip_resources_dir")
    patch_plugin(runtime_env / "uv.py", "_uv_resource_dir")


if __name__ == "__main__":
    main()
