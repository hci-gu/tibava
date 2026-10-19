# Upstream audio merge

This merge integrates upstream `2d31327` into the fork, retaining batch uploads,
saved/custom presets, scheduling and retry/cancellation, analyser upload caching,
clustering timelines, and filtered ELAN exports with the fork's current naming.

## Audio classification

New runs use upstream EAT (`worstchan/EAT-base_epoch30_finetune_AS2M`) with an
existing shot timeline. Run shot detection first, then select its output in
Audio classification. Batch sets can resolve the shot timeline from a previous
step or by timeline name. The defaults are `top_k=3` and `threshold=0.5`.

The EAT model returns multiple labels for each shot. Its fixed 1024-frame input
window retains upstream's truncation of longer shots, rather than aggregating
the entire shot. Stereo clips are downmixed to mono. Clips with no samples fail
the job; temporary clips are removed on success and failure. Model load errors
fail the job instead of generating fallback classifications.

Recreate saved audio presets using this shot-timeline input and run new batch
jobs. The old `segment_type` parameter and BEATs setup are removed. Existing
database rows and results are retained but are not converted or reprocessed.

## Configuration and migrations

The backend now reads `backend_config.toml`, selected by `DJANGO_CONFIG_PATH`.
The Compose files and ARM helper use that setting. The former JSON configuration
has been translated to TOML, including site hosts, CORS/CSRF origins, media URLs
and paths. Batch configuration lives under `[batch]`; corresponding uppercase
environment variables override it. Existing Compose concurrency overrides remain
in effect. An unset `max_batch_files` preserves the fork's unlimited file count.

Registration remains disabled. New users retain the fork's allowance of ten
videos and 10 GiB per video; existing quotas remain unchanged. Upstream terms
dialogs and configurable user/plugin limits are included.

Migrations `0030` and `0031` join the two histories and reconcile field defaults.
Use the normal backend migration command before starting new jobs:

```powershell
docker compose exec backend uv run --package backend python3 backend/src/backend/manage.py migrate
```

`scripts/verify_merge_migrations.py` checks a fresh in-memory SQLite database;
`--upgrade` starts at the fork's previous migration and verifies retained batch
records and user quotas. It never connects to the application database.

## Runtime and validation

The fork's CUDA Docker image, caches, readiness checks and isolated WhisperX
environment are retained. EAT uses a separate Ray environment with upstream's
Torch/Torchaudio 2.6 and timm requirements. It does not receive the CUDA 13 library
override used by the image's Torch 2.11 plugins. New upstream movie-pattern and
Ollama plugins remain available for single-video runs; their batch integration
is not enabled.

Local checks cover Python syntax, EAT input validation and output serialization,
batch timeline resolution, tensor windowing/thresholds, model retry/device
selection, temporary-file cleanup, single/batch ELAN exports, decoder behavior,
Django model/migration consistency, and both migration paths.

The combined backend run attempted 103 tests with SQLite and a local-memory
cache: 92 passed, and 11 scheduler tests failed because PostgreSQL advisory-lock
functions are unavailable in SQLite. The standalone EAF suite passed 21 tests,
the decoder suite passed one, and the inference/deployment suite passed ten.
The lockfile preserves the fork's existing versions while adopting renamed
workspace packages, rather than upgrading unrelated dependencies.

The Vue production build on Node 24 requires the existing Webpack compatibility
setting: `$env:NODE_OPTIONS = "--openssl-legacy-provider"` before `npm run build`.

Full runtime acceptance still requires a running PostgreSQL/cache/CUDA Docker
stack. SQLite cannot verify the scheduler's PostgreSQL advisory locks. Verify
batch upload → shot detection → EAT → timeline display → filtered ELAN export on
that stack, plus repeated EAT calls, WhisperX, representative non-audio plugins,
retry/cancellation and cache reuse. CPU/ARM runtime validation is deferred.
