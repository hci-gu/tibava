# Batch processing maintenance — 2026-09-17

## Current operational state

CUDA Compose processing services (`celery`, `analyser`, `inference_ray`) are
stopped. The web application, database and broker remain available. No volumes,
uploads, completed results or broker queues were deleted. Celery exceeded the
60-second shutdown grace period and exited 137; do not assume it drained cleanly.

The shared `/cache/batch-processing.paused` marker blocks batch ingest, scheduling
and plugin starts even if workers are inadvertently started. It was created with
`manage.py pause_batch_processing`. This is a batch dispatch guard, not a pause
for standalone plugin jobs: keep processing services stopped for full maintenance.

Batch `250a5bd4-5582-4bf9-882e-c4cd0df20357` retains 1,574 ingested videos,
1,547 completed thumbnails, 1,547 completed shot detections, and 115 completed
audio-RMS steps. One interrupted audio-RMS tracker,
`1448cd8d-905e-49cd-84bc-4e2779097b0a`, remains RUNNING with no linked PluginRun.
Its state has deliberately not been guessed/reset. There are 27 thumbnail errors
and their dependency-skipped descendants; all 27 source videos pass full decoding
with the corrected decoder, but their plugin results have not been regenerated.

## Changes

- Bulk tracker initialization (two read queries when rows already exist).
- Bounded, stage-ordered candidate selection rather than checking every video on
  each completion. PostgreSQL advisory locks serialize capacity decisions across
  batches; per-tracker locks prevent simultaneous duplicate plugin execution.
- Batch/user/global defaults are all four concurrent jobs, configurable through
  settings JSON or the matching uppercase environment variables. Prefetch is one.
- Failed broker publication releases its claimed slot. New PluginRuns are linked
  to trackers before execution for subsequent interrupted-work reconciliation.
- Retry restores dependency-failed skips while retaining completed/cancelled work.
- Video filter graphs use decoded-frame metadata, fixing missing pixel formats;
  metadata containers close correctly, filter tails flush, and partial inference
  batches are no longer discarded.
- CUDA model replicas scale to zero while idle. GPU models are capped at one
  replica, CPU autoscaling at four, with a one-request scaling target. GPU
  reservations were added for GPU-capable audio-gender and text plugins.
- TransNet uses CUDA; WhisperX alignment uses its selected inference device.

The selected preset still includes both Whisper and WhisperX. Removing either
would change requested outputs, so no selections were silently changed. Stage
barriers remain to avoid simultaneous residency of all large models.

## Verification and limits

Backend tests and decoder regression tests exercise these paths without running
the real batch. A synthetic 1,574-item/13-step scheduler test dispatched four
mocked jobs in 0.46 seconds / 28 queries; previous observed live ticks were about
12 seconds. This is not an end-to-end throughput measurement. CUDA Compose and
the installed Ray Serve schema validate. Actual GPU throughput, model cold starts
and memory peaks still require an explicitly approved live test.

## Before any future resume

1. Keep Celery stopped and retain the pause marker while inspecting the interrupted
   tracker, PluginRuns for its video, persisted outputs and queued messages. Do not
   bulk-reset RUNNING or DONE rows. New links help future reconciliation, but the
   old unlinked tracker requires manual review.
2. Decide whether to retry the 27 failed thumbnails and dependency-skipped steps,
   and whether both transcription plugins are still desired. Never purge broker
   queues or delete uploaded videos as a recovery shortcut.
3. Start the CUDA inference service with this configuration and verify health;
   start analyser only after Ray is healthy. This has NOT been done during this fix.
4. After explicit authorization and reconciliation, remove only the pause marker
   and explicitly enqueue the intended batch scheduler/ingest tasks. Tasks consumed
   while paused return without scheduling; removal alone does not resume them.
5. Start Celery and measure a small controlled run before resuming the full batch.
   Verify completed rows remain untouched and monitor CPU, GPU, VRAM and errors.

No automatic resume command is provided: reconciliation and the user's choice
must come first.
