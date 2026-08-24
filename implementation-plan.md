# Batch Video Processing Implementation Plan

## Goal

Add first-class support for uploading and processing batches of videos, including zip uploads with preserved folder structure, a better batch overview in the UI, and automatic execution of predefined plugin presets across all uploaded videos.

## Current Baseline

- [x] Single-video upload exists at `backend/src/backend/backend/views/video.py`.
- [x] Single-video upload validates extension and size, writes one media file, extracts metadata, creates one `Video`, then starts `thumbnail` plus selected analysers.
- [x] The frontend upload modal only accepts one video file.
- [x] The home view already supports selecting multiple videos and running a plugin across them.
- [x] Batch plugin execution currently loops over selected video ids in the browser and submits normal single-video plugin runs.
- [x] CLI-only helpers exist for adding videos from folders and running one plugin over many ids.

## Phase 1: Refactor Single-Video Ingest

- [x] Extract reusable backend ingest logic from `VideoUpload.post`.
- [x] Create a service/helper that accepts a file-like object, user, title/name, and optional source metadata.
- [x] Validate allowed extensions in one shared place.
- [x] Validate per-file size in one shared place.
- [x] Save the uploaded video using the existing UUID media path layout.
- [x] Extract video metadata with the same behavior as today: fps, duration, width, and height.
- [x] Create the `Video` row.
- [x] Return structured success and error results instead of direct HTTP responses.
- [x] Update the existing `/video/upload` endpoint to use the new ingest helper.
- [x] Confirm existing single-video upload behavior is unchanged.

## Phase 2: Add Batch Data Model

- [x] Add a `VideoBatch` model.
- [x] Add `VideoBatch.owner`.
- [x] Add `VideoBatch.name`.
- [x] Add `VideoBatch.status`.
- [x] Add `VideoBatch.date`.
- [x] Add counters for total, ready, failed, and completed items.
- [x] Add an optional field for selected plugin preset.
- [x] Add a `VideoBatchItem` model.
- [x] Add `VideoBatchItem.batch`.
- [x] Add `VideoBatchItem.video`, nullable until ingest succeeds.
- [x] Add `VideoBatchItem.original_filename`.
- [x] Add `VideoBatchItem.original_path` for zip-relative or folder-relative path.
- [x] Add `VideoBatchItem.file_size`.
- [x] Add `VideoBatchItem.checksum`.
- [x] Add `VideoBatchItem.ingest_status`.
- [x] Add `VideoBatchItem.ingest_error`.
- [x] Add timestamps for created and updated state.
- [x] Add model `to_dict()` methods matching the existing API style.
- [x] Create migrations.

## Phase 3: Batch Upload API

- [x] Add `POST /video/batch/upload`.
- [x] Add `GET /video/batch/list`.
- [x] Add `GET /video/batch/get?id=...`.
- [x] Add `POST /video/batch/retry-failed`.
- [x] Add `POST /video/batch/delete`.
- [x] Support multiple video files in one multipart request.
- [x] Support one zip file in one multipart request.
- [x] Return `batch_id` immediately after upload request validation.
- [x] Return per-item ingest state from batch detail API.
- [x] Include linked `Video.to_dict()` data for successfully ingested items.
- [x] Enforce ownership checks on every batch endpoint.

## Phase 4: Zip Handling

- [x] Add a safe zip extraction helper.
- [x] Reject zip entries with absolute paths.
- [x] Reject zip entries containing path traversal.
- [x] Ignore or reject directories.
- [x] Ignore or reject unsupported video extensions.
- [x] Preserve zip-relative folder paths in `VideoBatchItem.original_path`.
- [x] Add a max number of files per batch.
- [x] Add a max total uncompressed size per batch.
- [x] Add a max individual uncompressed file size.
- [x] Add clear per-file errors for rejected zip entries.
- [x] Add tests for malicious zip paths.
- [x] Add tests for unsupported files in zip archives.

## Phase 5: Async Batch Ingest

- [x] Add a Celery task for ingesting a batch.
- [x] Store uploaded batch source files in a temporary batch upload directory.
- [x] Make `/video/batch/upload` enqueue the ingest task instead of doing all metadata extraction in the request.
- [x] Mark batch items as `PENDING`, `INGESTING`, `READY`, or `ERROR`.
- [x] Mark the batch as `UPLOADING`, `INGESTING`, `READY`, `PARTIAL_ERROR`, or `ERROR`.
- [x] Update batch counters as items are processed.
- [x] Make ingest idempotent so retry does not duplicate already-created videos.
- [x] Clean up temporary batch upload files after successful ingest.
- [x] Preserve failed files long enough for debugging or retry, or record enough error detail to delete them safely.

## Phase 6: Plugin Presets

- [x] Define a backend representation for plugin presets.
- [x] Start with static config before adding a database editor.
- [x] Include plugin name, parameters, and optional dependency mapping per step.
- [x] Always include `thumbnail` unless explicitly disabled.
- [x] Add a default preset for common batch processing.
- [x] Validate all plugin names against `PluginManager`.
- [x] Validate preset parameters with each plugin parser.
- [x] Represent dependencies explicitly, for example `shotdetection.timelines.shots`.
- [x] Add a service that resolves dependency outputs per video.

## Phase 7: Batch Plugin Runner

- [x] Add `POST /video/batch/run-preset`.
- [x] Add a Celery task that runs a preset for all ready videos in a batch.
- [x] Schedule plugin runs server-side instead of looping in the browser.
- [x] Use existing `PluginManager` to create normal `PluginRun` rows.
- [x] Track batch-level plugin progress.
- [x] Track per-item plugin progress.
- [x] Do not stop the whole batch when one video fails.
- [x] Support retrying failed plugin steps.
- [x] Add backpressure so a batch does not enqueue hundreds or thousands of plugin runs at once.
- [x] Add a configurable max number of active plugin runs per batch.
- [x] Add dependency handling for plugins that need timeline ids.
- [x] Ensure `shotdetection` can feed `shot_type_classification` automatically.
- [x] Ensure plugins that require user-provided files are either excluded from automatic presets or handled explicitly.

## Phase 8: Improve Batch Overview UI

- [x] Add a batch list view.
- [x] Add a batch detail view.
- [x] Add a table-oriented video overview for large batches.
- [x] Show folder structure from `VideoBatchItem.original_path`.
- [x] Add filters for all, ingesting, ready, running, failed, and complete items.
- [x] Add search by name/path.
- [x] Show duration, resolution, upload date, ingest status, and plugin status.
- [x] Show batch-level progress.
- [x] Show per-video plugin progress.
- [x] Add retry failed action.
- [x] Add run preset action.
- [x] Add delete batch action.
- [x] Keep the existing card gallery usable for smaller libraries.
- [x] Avoid polling every video separately; poll batch state from batch APIs.

## Phase 9: Upload UI

- [x] Update `ModalVideoUpload.vue` or add a new batch upload modal.
- [x] Add mode selection for single video, multiple videos, and zip archive.
- [x] Use `multiple` file input for multi-video uploads.
- [x] Accept `.zip` for archive uploads.
- [x] Show selected file count and total size before upload.
- [x] Let user choose a plugin preset before upload.
- [x] Let user choose whether preset execution starts automatically after ingest.
- [x] Show upload progress for the source upload.
- [x] Redirect to batch detail after the upload request succeeds.

## Phase 10: Operational Guardrails

- [x] Add configurable max files per batch.
- [x] Add configurable max total batch size.
- [x] Add configurable max zip uncompressed size.
- [x] Add configurable max active batch ingests per user.
- [x] Add configurable max active plugin runs per batch.
- [x] Add duplicate detection by checksum within a batch.
- [x] Decide whether duplicate detection should apply across all user videos.
- [x] Improve startup recovery for batch ingest and batch plugin execution.
- [x] Add cancellation semantics if needed.
- [x] Add cleanup for abandoned temporary upload directories.
- [x] Review analyser upload behavior because every plugin currently uploads the video file again. Finding: `Task.upload_video` still uploads per plugin invocation.
- [x] Consider caching analyser-side video data ids per `Video` to avoid repeated uploads. Decision: defer cache persistence until analyser data-id lifetime and invalidation are defined.

## Phase 11: Tests

- [x] Add tests for the shared single-video ingest helper.
- [x] Add tests for preserving existing `/video/upload` behavior.
- [x] Add tests for multi-file batch upload.
- [x] Add tests for zip batch upload.
- [x] Add tests for folder path preservation.
- [x] Add tests for malformed zip archives.
- [x] Add tests for path traversal in zip archives.
- [x] Add tests for batch ownership restrictions.
- [x] Add tests for preset validation.
- [x] Add tests for dependency resolution between preset steps.
- [x] Add tests for retrying failed ingest items.
- [x] Add tests for retrying failed plugin steps.

## Phase 12: Rollout Strategy

- [x] Ship the ingest refactor first with no user-visible behavior change.
- [x] Ship batch models and read-only APIs.
- [x] Ship multi-file upload.
- [x] Ship zip upload.
- [x] Ship batch overview UI.
- [x] Ship static plugin presets.
- [x] Ship automatic preset execution.
- [x] Ship retry and cleanup tools.
- [x] Move CLI helpers to use the same shared ingest and preset services.

## Open Decisions

- [x] Should user allowance count individual videos, batches, total storage, or a combination? Decision: keep current per-video allowance semantics for now; batch limits are separate operational limits.
- [x] Should zip-relative paths be editable after upload? Decision: preserve them as immutable upload provenance for now.
- [x] Should duplicate videos be rejected, skipped, or linked to the same existing `Video`? Decision: reject duplicate checksums within the same batch; do not deduplicate across prior user videos yet.
- [x] Should batch presets be global config, admin-managed database rows, or user-defined? Decision: start with global static backend config.
- [x] Which plugins belong in the default automatic preset? Decision: `thumbnail`, `shotdetection`, and `shot_type_classification`.
- [x] How many concurrent analyser jobs should one batch be allowed to occupy? Decision: one per batch workflow for the initial implementation.
- [x] How long should failed temporary upload files be retained? Decision: keep failed source files until retry or batch delete; clean up successful item sources.
