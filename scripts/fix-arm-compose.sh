#!/usr/bin/env bash
set -euo pipefail

# Patch TIB-AV-A for Linux ARM64/aarch64 Docker Compose development.
# Fixes uv resolution failure caused by onnxruntime-gpu not publishing ARM64 Linux wheels.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

arch="$(uname -m)"
case "$arch" in
  aarch64|arm64) ;;
  *)
    echo "Warning: host architecture is '$arch', not ARM64/aarch64. Continuing anyway." >&2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not on PATH." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 plugin is required. Install docker-compose-plugin, then use 'docker compose'." >&2
  exit 1
fi

PYPROJECT="inference_ray/pyproject.toml"
if [[ ! -f "$PYPROJECT" ]]; then
  echo "ERROR: $PYPROJECT not found. Run this from the tibava repository." >&2
  exit 1
fi

if [[ ! -f "$PYPROJECT.bak" ]]; then
  cp "$PYPROJECT" "$PYPROJECT.bak"
fi

python3 - <<'PY'
from pathlib import Path
p = Path("inference_ray/pyproject.toml")
s = p.read_text()
old = '    "onnxruntime-gpu>=1.21.1",'
new = '''    # onnxruntime-gpu has no Linux ARM64/aarch64 wheels on PyPI.
    # Use GPU runtime only on Linux x86_64; use CPU runtime on ARM64.
    "onnxruntime-gpu>=1.21.1; sys_platform == 'linux' and platform_machine == 'x86_64'",
    "onnxruntime>=1.21.1; sys_platform == 'linux' and platform_machine == 'aarch64'",'''
if old in s:
    s = s.replace(old, new)
elif "onnxruntime-gpu>=1.21.1;" in s and "onnxruntime>=1.21.1;" in s:
    pass
else:
    raise SystemExit("ERROR: Could not find expected onnxruntime-gpu dependency line; patch manually.")
p.write_text(s)
PY

# The compose file bind-mounts pyproject.toml but not uv.lock into /app, so containers can resolve
# from the patched pyproject. Still update uv.lock locally when possible to keep repo state coherent.
if command -v uv >/dev/null 2>&1; then
  echo "Updating uv.lock with local uv..."
  uv lock || echo "Warning: uv lock failed; continuing because containers can resolve from pyproject.toml." >&2
else
  echo "uv not found locally; skipping uv.lock update. Containers can resolve from pyproject.toml."
fi

cat > Dockerfile.arm-uv <<'DOCKERFILE'
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Runtime libraries needed by Python packages in this project on the slim image.
# Wand requires ImageMagick's MagickWand shared library.
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    imagemagick \
    libmagickwand-6.q16-6 \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
  && rm -rf /var/lib/apt/lists/*
DOCKERFILE

cat > docker-compose.arm.yml <<'YAML'
# ARM64 override: use a Python 3.12 uv image with needed Debian runtime libs,
# and force uv to use the system interpreter. This prevents uv from trying to
# download python-build-standalone from GitHub at container start.
x-arm-uv-env: &arm_uv_env
  UV_PYTHON_DOWNLOADS: never
  UV_NO_MANAGED_PYTHON: "1"
  UV_PYTHON: /usr/local/bin/python3.12

services:
  inference_ray:
    image: tibava-arm-uv:latest
    build:
      context: .
      dockerfile: Dockerfile.arm-uv
    environment:
      <<: *arm_uv_env
      HF_HOME: /models
      NUMBA_CACHE_DIR: /tmp
    command: uv run --no-python-downloads --no-managed-python --python /usr/local/bin/python3.12 --package inference_ray serve run inference_ray/deploy.yml

  analyser:
    image: tibava-arm-uv:latest
    build:
      context: .
      dockerfile: Dockerfile.arm-uv
    environment:
      <<: *arm_uv_env
    command: uv run --no-python-downloads --no-managed-python --python /usr/local/bin/python3.12 --package analyser analyser/src/analyser/server.py -v -c analyser/config.yml

  celery:
    image: tibava-arm-uv:latest
    build:
      context: .
      dockerfile: Dockerfile.arm-uv
    environment:
      <<: *arm_uv_env
      TIBAVA_BACKEND_CONFIG: /app/backend/src/backend/config.json
    command: uv run --no-python-downloads --no-managed-python --python /usr/local/bin/python3.12 --package backend celery -A tibava worker -l INFO

  backend:
    image: tibava-arm-uv:latest
    build:
      context: .
      dockerfile: Dockerfile.arm-uv
    environment:
      <<: *arm_uv_env
      TIBAVA_BACKEND_CONFIG: /app/backend/src/backend/config.json
    command: uv run --no-python-downloads --no-managed-python --python /usr/local/bin/python3.12 --package backend python3 backend/src/backend/manage.py runserver 0.0.0.0:8000
YAML

mkdir -p data/cache data/analyser data/media data/tmp data/predictions data/backend_cache data/db

echo
echo "ARM patch applied. Next commands:"
echo "  docker compose -f docker-compose.yml -f docker-compose.arm.yml build"
echo "  docker compose -f docker-compose.yml -f docker-compose.arm.yml up"
echo
echo "If DNS still fails for other downloads, fix Docker/container DNS on the host."
echo "If you need GPU acceleration, use an x86_64 Linux NVIDIA machine instead; ARM64 will use CPU onnxruntime."
