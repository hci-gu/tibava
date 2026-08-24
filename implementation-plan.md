# Batch Video Workflow Next Steps

## Goal

Move the initial batch upload implementation from a functional prototype to a reliable workflow for uploading hundreds of videos, preserving folder structure, and running predefined plugin presets with clear progress and recovery behavior.

## Phase 1: Smoke Test Seeded Batch

- [x] Log in with the seeded test account `test@email.com`.
- [ ] Open `/batches/3088719230d94c0fa6bc8623ca353a98`.
- [x] Confirm the seeded batch API returns 7 ready videos.
- [ ] Confirm the seeded batch renders with 7 ready videos.
- [x] Confirm folder/path values are present in the batch detail API response.
- [ ] Confirm folder/path values render in the batch detail table.
- [x] Confirm each copied video has a linked `Video` row for the existing video analysis view.
- [ ] Confirm each copied video opens in the existing video analysis view.
- [x] Trigger **Run preset** on the seeded batch.
- [x] Confirm plugin run rows appear for each batch item.
- [x] Confirm batch plugin status changes while jobs run.
- [x] Confirm `shotdetection` completes for at least one seeded video.
- [x] Confirm `shot_type_classification` receives the shot timeline from `shotdetection`.
- [x] Record any UI, backend, Celery, or analyser errors found during the smoke test.

Smoke test notes:

- Browser/plugin automation was unavailable in this environment, so UI-rendering-only checks remain open.
- Initial Celery worker discarded `backend.tasks.batch.run_video_batch_preset` until the worker was restarted with the new task module loaded.
- `AppConfig.ready()` treated batch task arguments like plugin task arguments and crashed on UUID args.
- `AppConfig.ready()` could mutate batch/plugin state during ordinary `manage.py shell` verification commands.
- Retry of failed preset steps did not reconstruct outputs from completed dependency steps.
- One seeded video, `user-3/Tagesschau-oil.mp4`, repeatedly fails in `thumbnail_generator` because PyAV raises `InvalidDataError` while decoding a packet.

## Phase 2: Fix Smoke Test Issues

- [ ] Fix any batch detail rendering errors found in Phase 1.
- [ ] Fix any batch API response shape issues found in Phase 1.
- [x] Fix any preset execution errors found in Phase 1.
- [x] Fix any dependency handoff errors between preset steps.
- [x] Fix any Celery or analyser status synchronization issues.
- [x] Re-run the seeded-batch smoke test after fixes.
- [ ] Fix or deliberately exclude the `thumbnail_generator` decode failure for `user-3/Tagesschau-oil.mp4`.

## Phase 3: Test Real Batch Uploads

- [ ] Upload multiple video files through the batch upload modal.
- [x] Upload multiple video files through the batch upload API.
- [x] Confirm the upload request returns a batch id immediately.
- [x] Confirm ingest happens asynchronously after upload.
- [x] Confirm all successful files appear as videos in the batch.
- [x] Confirm successful videos also appear in the normal video library.
- [x] Upload a zip archive containing nested folders.
- [x] Confirm zip-relative paths are preserved.
- [x] Upload a zip containing unsupported files.
- [x] Confirm unsupported files show as failed batch items.
- [x] Upload a malformed zip archive.
- [x] Confirm a clear batch/item error is shown.
- [x] Test retrying failed ingest items.
- [x] Test deleting a batch.
- [x] Test cancelling a running batch.

Upload test notes:

- Multi-file upload via API succeeds, but folder metadata for non-zip uploads still needs a browser/modal test because curl-based multipart quoting did not reliably represent frontend `FormData`.
- Zip upload preserved `folder-a/clip-a.mp4` and `folder-b/nested/clip-b.mp4`.
- Unsupported zip entries show `wrong_file_extension`.
- Malformed zip uploads become a batch with `malformed_zip`.
- Corrupt `.mp4` retry is accepted and reprocesses to the same ingest failure.
- Cancellation now leaves the batch in `CANCELLED` with pending/ingesting items marked `cancelled`.

## Phase 4: Harden Backend Execution

- [ ] Replace the simple sequential preset runner with an explicit batch scheduler.
- [x] Add a durable queued/running/done state per preset step.
- [ ] Add configurable parallelism for plugin runs per batch.
- [ ] Add global backpressure so one batch cannot monopolize analyser capacity.
- [ ] Add per-user active batch limits that account for both ingest and plugin execution.
- [x] Make preset execution resumable after backend or Celery restart.
- [x] Make retry failed plugin steps resume from the correct failed step.
- [x] Add cancellation behavior for queued ingest and queued/running batch plugin rows.
- [ ] Add cancellation behavior for already-started plugin runs if supported by the analyser.
- [ ] Add clear handling for videos deleted while a batch is running.
- [x] Add cleanup for old successful batch source files and abandoned temp directories in scheduled operations.

## Phase 5: Make Presets Product-Ready

- [x] Decide the production default plugin subset.
- [x] Decide whether presets are global config, admin-managed database rows, or user-defined.
- [x] Add a preset list API that includes user-facing names and descriptions.
- [x] Add preset validation at upload time and run time.
- [x] Add support for preset steps with fixed optional parameters.
- [x] Exclude plugins that require per-run uploaded files unless a batch-safe parameter strategy exists.
- [ ] Add an admin or config workflow for editing presets.
- [x] Add documentation for preset dependency expressions.

Preset decision notes:

- Presets remain code-defined global config for now, via `backend.utils.plugin_presets`.
- The production default subset is `thumbnail`, `shotdetection`, and `shot_type_classification`.
- User-editable or admin-editable presets remain a later product decision.

## Phase 6: Add Integration Tests

- [x] Add database-backed tests for `VideoBatch` and `VideoBatchItem`.
- [x] Add API tests for multi-file batch upload.
- [x] Add API tests for zip batch upload.
- [x] Add API tests for batch list and detail ownership restrictions.
- [x] Add API tests for retry ingest, retry plugin steps, cancel, and delete.
- [x] Add Celery eager-mode tests for batch ingest.
- [x] Add Celery eager-mode tests for preset execution.
- [x] Add dependency resolution tests using real `Timeline` rows.
- [ ] Add a scripted Docker smoke test for full upload-to-ingest behavior.
- [ ] Add a scripted Docker smoke test for preset execution against the analyser.

Integration test notes:

- `backend.tests` now includes 30 tests covering model counters, upload/list/detail/action APIs, ingest task execution, preset execution, and timeline dependency reconstruction.

## Phase 7: Improve Batch UI

- [x] Add a true folder tree or grouped table view for zip-relative paths.
- [x] Add stable per-plugin columns instead of only compact plugin chips.
- [x] Add clearer batch-level status summaries.
- [x] Add visible counts for pending, ingesting, ready, failed, running, done, and skipped items.
- [x] Add pagination or virtual scrolling for hundreds of rows.
- [x] Add bulk selection within a batch.
- [x] Add actions scoped to selected rows.
- [x] Add clearer error messages and retry affordances.
- [x] Add confirmation dialogs for destructive batch actions.
- [x] Add loading and empty states for batch list and detail views.

UI implementation notes:

- Batch detail now groups rows by folder path, uses one stable column per plugin, defaults to 50 rows per page, supports row selection, and has confirmation dialogs for run/cancel/delete.
- Batch list now includes plugin done/failed counts plus loading and empty table states.
- Frontend build passes; visual browser QA remains open in the verification log.

## Phase 8: Improve Analyser Efficiency

- [ ] Measure how much time repeated analyser-side video upload adds per preset.
- [x] Decide the lifetime and invalidation rules for analyser-side uploaded video data ids.
- [x] Add a persistent analyser data-id cache per `Video`.
- [x] Reuse cached analyser video ids across plugin steps when valid.
- [x] Invalidate cached analyser ids when media files are deleted or replaced.
- [x] Add metrics/logging for analyser upload cache hits and misses.
- [ ] Re-test preset runtime before and after caching.

Analyser cache notes:

- `Video` now stores the analyser data id alongside the media file UUID and extension.
- `Task.upload_video()` logs cache hits/misses and reuses the cached analyser id when the media identity is unchanged.
- Deleting a `Video` invalidates the cache with the row. Future in-place media replacement must clear the analyser cache fields or change the media file UUID.

## Phase 9: Documentation And Rollout

- [x] Document the batch upload API contract.
- [x] Document zip upload constraints and path-preservation behavior.
- [x] Document batch status and item status meanings.
- [x] Document preset behavior and dependency rules.
- [x] Document operational limits and relevant settings.
- [x] Document the seeded test account and seeded batch fixture.
- [x] Add manual QA steps for release testing.
- [ ] Decide whether to keep or remove the seeded test account before production deployment.

## Verification Log

- [x] `python -m compileall backend/src/backend/backend`
- [x] `python3 backend/src/backend/manage.py test backend.tests`
- [x] `NODE_OPTIONS=--openssl-legacy-provider npm run build`
- [x] Docker API smoke: seeded account login, seeded batch get, preset run/retry, multi-file upload, zip upload, malformed zip, retry failed ingest, delete batch, cancel batch.
- [ ] Browser UI smoke with Playwright or browser plugin.
