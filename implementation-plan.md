# Batch Video Workflow Next Steps

## Goal

Move the initial batch upload implementation from a functional prototype to a reliable workflow for uploading hundreds of videos, preserving folder structure, and running predefined plugin presets with clear progress and recovery behavior.

## Phase 1: Smoke Test Seeded Batch

- [ ] Log in with the seeded test account `test@email.com`.
- [ ] Open `/batches/3088719230d94c0fa6bc8623ca353a98`.
- [ ] Confirm the seeded batch renders with 7 ready videos.
- [ ] Confirm folder/path values render in the batch detail table.
- [ ] Confirm each copied video opens in the existing video analysis view.
- [ ] Click **Run preset** on the seeded batch.
- [ ] Confirm plugin run rows appear for each batch item.
- [ ] Confirm batch plugin status refreshes while jobs run.
- [ ] Confirm `shotdetection` completes for at least one seeded video.
- [ ] Confirm `shot_type_classification` receives the shot timeline from `shotdetection`.
- [ ] Record any UI, backend, Celery, or analyser errors found during the smoke test.

## Phase 2: Fix Smoke Test Issues

- [ ] Fix any batch detail rendering errors found in Phase 1.
- [ ] Fix any batch API response shape issues found in Phase 1.
- [ ] Fix any preset execution errors found in Phase 1.
- [ ] Fix any dependency handoff errors between preset steps.
- [ ] Fix any Celery or analyser status synchronization issues.
- [ ] Re-run the seeded-batch smoke test after fixes.

## Phase 3: Test Real Batch Uploads

- [ ] Upload multiple video files through the batch upload modal.
- [ ] Confirm the upload request returns a batch id immediately.
- [ ] Confirm ingest happens asynchronously after upload.
- [ ] Confirm all successful files appear as videos in the batch.
- [ ] Confirm successful videos also appear in the normal video library.
- [ ] Upload a zip archive containing nested folders.
- [ ] Confirm zip-relative paths are preserved.
- [ ] Upload a zip containing unsupported files.
- [ ] Confirm unsupported files show as failed batch items.
- [ ] Upload a malformed zip archive.
- [ ] Confirm a clear batch/item error is shown.
- [ ] Test retrying failed ingest items.
- [ ] Test deleting a batch.
- [ ] Test cancelling a running batch.

## Phase 4: Harden Backend Execution

- [ ] Replace the simple sequential preset runner with an explicit batch scheduler.
- [ ] Add a durable queued/running/done state per preset step.
- [ ] Add configurable parallelism for plugin runs per batch.
- [ ] Add global backpressure so one batch cannot monopolize analyser capacity.
- [ ] Add per-user active batch limits that account for both ingest and plugin execution.
- [ ] Make preset execution resumable after backend or Celery restart.
- [ ] Make retry failed plugin steps resume from the correct failed step.
- [ ] Add cancellation behavior for already-started plugin runs if supported by the analyser.
- [ ] Add clear handling for videos deleted while a batch is running.
- [ ] Add cleanup for old successful batch source files and abandoned temp directories in scheduled operations.

## Phase 5: Make Presets Product-Ready

- [ ] Decide the production default plugin subset.
- [ ] Decide whether presets are global config, admin-managed database rows, or user-defined.
- [ ] Add a preset list API that includes user-facing names and descriptions.
- [ ] Add preset validation at upload time and run time.
- [ ] Add support for preset steps with optional parameters.
- [ ] Exclude plugins that require per-run uploaded files unless a batch-safe parameter strategy exists.
- [ ] Add an admin or config workflow for editing presets.
- [ ] Add documentation for preset dependency expressions.

## Phase 6: Add Integration Tests

- [ ] Add database-backed tests for `VideoBatch` and `VideoBatchItem`.
- [ ] Add API tests for multi-file batch upload.
- [ ] Add API tests for zip batch upload.
- [ ] Add API tests for batch list and detail ownership restrictions.
- [ ] Add API tests for retry ingest, retry plugin steps, cancel, and delete.
- [ ] Add Celery eager-mode tests for batch ingest.
- [ ] Add Celery eager-mode tests for preset execution.
- [ ] Add dependency resolution tests using real `Timeline` rows.
- [ ] Add a scripted Docker smoke test for full upload-to-ingest behavior.
- [ ] Add a scripted Docker smoke test for preset execution against the analyser.

## Phase 7: Improve Batch UI

- [ ] Add a true folder tree or grouped table view for zip-relative paths.
- [ ] Add stable per-plugin columns instead of only compact plugin chips.
- [ ] Add clearer batch-level status summaries.
- [ ] Add visible counts for pending, ingesting, ready, failed, running, done, and skipped items.
- [ ] Add pagination or virtual scrolling for hundreds of rows.
- [ ] Add bulk selection within a batch.
- [ ] Add actions scoped to selected rows.
- [ ] Add clearer error messages and retry affordances.
- [ ] Add confirmation dialogs for destructive batch actions.
- [ ] Add loading and empty states for batch list and detail views.

## Phase 8: Improve Analyser Efficiency

- [ ] Measure how much time repeated analyser-side video upload adds per preset.
- [ ] Decide the lifetime and invalidation rules for analyser-side uploaded video data ids.
- [ ] Add a persistent analyser data-id cache per `Video`.
- [ ] Reuse cached analyser video ids across plugin steps when valid.
- [ ] Invalidate cached analyser ids when media files are deleted or replaced.
- [ ] Add metrics/logging for analyser upload cache hits and misses.
- [ ] Re-test preset runtime before and after caching.

## Phase 9: Documentation And Rollout

- [ ] Document the batch upload API contract.
- [ ] Document zip upload constraints and path-preservation behavior.
- [ ] Document batch status and item status meanings.
- [ ] Document preset behavior and dependency rules.
- [ ] Document operational limits and relevant settings.
- [ ] Document the seeded test account and seeded batch fixture.
- [ ] Add manual QA steps for release testing.
- [ ] Decide whether to keep or remove the seeded test account before production deployment.

