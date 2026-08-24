# Batch Video Workflow Implementation Plan

## Goal

Support batch uploads of hundreds of videos, preserve useful folder context after upload, and run a predefined plugin subset automatically with predictable progress, retry, cancellation, and operational limits.

## Current Baseline

- Single-video upload and plugin execution are working well enough for normal use.
- Batch upload API support exists for multi-file uploads and zip uploads.
- Zip uploads preserve relative folder paths.
- Batch list/detail views exist and include status summaries, grouped rows, plugin columns, pagination, selection, retry, run, cancel, and delete actions.
- Code-defined presets exist for `thumbnail`, `shotdetection`, and `shot_type_classification`.
- Batch ingest and preset execution are covered by Django tests.
- Repeated analyser video uploads are now cached per `Video` when the media identity is unchanged.

## 1. Browser-Verified Batch Upload UX

- [x] Run a browser smoke test for the batch list and batch detail pages.
- [x] Confirm the seeded batch `3088719230d94c0fa6bc8623ca353a98` renders with 7 ready videos.
- [x] Confirm folder/path values render correctly in the batch detail table.
- [x] Confirm each copied video opens from the batch detail table into the existing video analysis view.
- [x] Upload multiple loose video files through the batch upload modal.
- [x] Confirm loose-file batch uploads preserve the frontend-provided display paths where available.
- [x] Upload a nested zip through the batch upload modal.
- [x] Confirm zip folder grouping works in the UI for nested paths.
- [x] Fix any browser-only layout, routing, or API shape issues found during the smoke test.

## 2. Automatic Preset Execution

- [x] Add an option to run a selected preset automatically after batch ingest completes.
- [x] Persist the selected preset on the batch at upload time.
- [x] Start preset execution only after all ingestable files have reached a terminal ingest state.
- [x] Make partially failed ingest batches eligible to run presets for successfully ingested videos.
- [x] Show the selected preset and auto-run state in the batch detail header.
- [x] Add tests for upload-with-preset and auto-run-after-ingest behavior.

## 3. Explicit Batch Scheduler

- [x] Replace the current simple sequential preset runner with an explicit scheduler loop.
- [x] Add configurable per-batch plugin parallelism.
- [x] Add configurable global analyser backpressure so one large batch cannot consume all worker capacity.
- [x] Add per-user active batch limits that account for ingest and plugin execution.
- [x] Ensure retry jobs enter the same scheduler path as initial jobs.
- [x] Make scheduler state recover cleanly after backend, Celery, or analyser restarts.
- [x] Add tests for scheduler ordering, limits, retries, and restart recovery.

## 4. Cancellation, Deletion, And Edge Cases

- [x] Add cancellation behavior for already-started analyser plugin runs if the analyser exposes a supported cancel operation.
- [x] Add clear handling for videos deleted while a batch is queued or running.
- [x] Decide whether the known `thumbnail_generator` decode failure for `user-3/Tagesschau-oil.mp4` should be fixed, skipped, or documented as an analyser limitation.
- [x] Add user-facing error messages for skipped/deleted/cancelled plugin work.
- [x] Add regression tests for deleted-video and mid-plugin-cancel behavior.

## 5. Preset Management

- [x] Decide whether production preset editing should be code-configured, admin-managed, or user-managed.
- [x] Record admin-managed preset models and APIs as intentionally out of scope for this rollout.
- [x] If code-configured, add deployment documentation for changing preset definitions safely.
- [x] Validate preset dependencies before a preset can be saved or exposed.
- [x] Add tests for invalid preset definitions and dependency cycles.

## 6. Docker Smoke Tests

- [x] Add a scripted Docker smoke test for full upload-to-ingest behavior.
- [x] Add a scripted Docker smoke test for preset execution against the analyser.
- [x] Include login using `test@email.com` / `password123`.
- [x] Include loose multi-file upload, nested zip upload, preset run, retry failed work, cancel running batch, and delete batch.
- [x] Document expected smoke-test runtime and required local services.

## 7. Performance And Observability

- [x] Measure baseline preset runtime for the seeded batch before relying on analyser upload caching.
- [x] Measure preset runtime after analyser upload caching.
- [x] Add structured logs or metrics for batch ingest duration, queue wait time, plugin runtime, retry count, and cache hit/miss counts.
- [x] Add a compact operational dashboard or admin view for stuck/running batches if needed.
- [x] Define alert thresholds for batches stuck in ingesting/running states.

## 8. Rollout Readiness

- [x] Decide whether to keep or remove the seeded test account before production deployment.
- [x] Add release QA instructions that cover browser UI, API, worker restart recovery, and analyser availability.
- [x] Document recommended limits for maximum files per batch, maximum zip size, and concurrent active batches.
- [x] Add migration/rollback notes for the analyser cache fields and any scheduler fields.
- [x] Run the full backend test suite, frontend build, Docker smoke scripts, and browser smoke test before release.
