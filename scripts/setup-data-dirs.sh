#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

dirs="
data/analyser
data/backend_cache
data/cache
data/db
data/media
data/models
data/predictions
data/tmp
"

for dir in $dirs; do
  if [ -d "$dir" ]; then
    printf 'OK      %s\n' "$dir"
  else
    mkdir -p "$dir"
    printf 'CREATED %s\n' "$dir"
  fi
done

if [ -z "$(find data/models -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
  printf '\nWARNING: data/models is empty. Download and extract models before running analysis workloads.\n'
  printf 'See README.md for the models.tar.gz download step.\n'
fi
