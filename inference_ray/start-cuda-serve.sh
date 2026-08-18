#!/bin/sh
set -eu

if [ ! -f /models/nltk_data/tokenizers/punkt_tab/english/abbrev_types.txt ]; then
    echo "Missing persistent NLTK punkt_tab data under /models/nltk_data" >&2
    exit 1
fi

runtime_cache=/var/cache/ray/runtime_resources
mkdir -p "$runtime_cache"

# Ray tracks cached environments only in memory. Register valid environments
# from the persistent volume before its runtime-env agent starts.
.venv/bin/python inference_ray/patch-runtime-env-cache.py
.venv/bin/python inference_ray/prepare-cuda-deploy.py \
  inference_ray/deploy.cuda.yml \
  /tmp/deploy.cuda.runtime.yml

.venv/bin/ray start \
  --head \
  --dashboard-host=0.0.0.0 \
  --dashboard-port=52365 \
  --dashboard-agent-listen-port=52366 \
  --num-gpus=1 \
  --disable-usage-stats

session_dir=$(readlink -f /tmp/ray/session_latest)
rm -rf "$session_dir/runtime_resources"
ln -s "$runtime_cache" "$session_dir/runtime_resources"

exec .venv/bin/serve run \
  --address=auto \
  /tmp/deploy.cuda.runtime.yml
