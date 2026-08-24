# Batch Video Workflow

This document describes the current batch upload workflow for operators, QA, and developers.

## API Contract

All endpoints require an authenticated session and CSRF token.

- `POST /api/video/batch/upload`
  - Multipart form data.
  - Accepts either `files` with one or more video files, `file` with a single video or zip, or `zip` with a zip archive.
  - Optional fields: `name`, `preset`, `auto_run_preset`, `paths`.
  - `paths` may be a JSON array matching uploaded file order or a JSON object keyed by uploaded filename.
  - Returns immediately with `{ "status": "ok", "batch_id": "<hex uuid>" }`; ingest runs asynchronously.

- `GET /api/video/batch/list`
  - Returns all batches owned by the authenticated user.

- `GET /api/video/batch/get?id=<hex uuid>`
  - Returns one owned batch with item rows, linked videos, and batch plugin rows.

- `POST /api/video/batch/run-preset`
  - JSON body: `{ "id": "<hex uuid>", "preset": "default_batch_analysis" }`.
  - Enqueues preset execution for ready videos.

- `POST /api/video/batch/retry-failed`
  - JSON body: `{ "id": "<hex uuid>" }`.
  - Requeues failed ingest items that still have a source file.

- `POST /api/video/batch/retry-failed-plugin-steps`
  - JSON body: `{ "id": "<hex uuid>", "preset": "default_batch_analysis" }`.
  - Requeues failed batch plugin steps and resumes completed dependencies where possible.

- `POST /api/video/batch/cancel`
  - JSON body: `{ "id": "<hex uuid>" }`.
  - Marks queued ingest items as cancelled and queued/running batch plugin rows as skipped.

- `POST /api/video/batch/delete`
  - JSON body: `{ "id": "<hex uuid>" }`.
  - Deletes the batch row and its temporary source directory.

## Zip Uploads

Zip uploads preserve each member path after normalizing separators to `/`.

Rejected zip entries become failed batch items:

- `unsafe_zip_path`: empty paths, absolute paths, parent traversal, or drive-prefixed paths.
- `wrong_file_extension`: non-video files.
- `too_many_files`: archive contains more valid video entries than `MAX_BATCH_FILES`.
- `file_too_large`: entry exceeds `MAX_BATCH_FILE_SIZE` when configured.
- `batch_too_large`: accepted entry sizes would exceed `MAX_BATCH_TOTAL_SIZE`.
- `malformed_zip`: archive cannot be opened as a zip.

Successful entries are extracted to the batch temp directory, ingested into normal `Video` rows, and then the extracted source file is removed.

## Status Meanings

Batch statuses:

- `UPLOADING`: batch row exists and upload handling is still preparing sources.
- `INGESTING`: async ingest task is creating `Video` rows.
- `READY`: all items ingested successfully and no batch plugin errors are present.
- `PARTIAL_ERROR`: at least one item or plugin step failed while other work succeeded.
- `ERROR`: the batch cannot proceed or every item failed.
- `RUNNING`: preset plugin execution is in progress.
- `CANCELLED`: the user cancelled queued batch work.

Item statuses:

- `PENDING`: waiting for ingest.
- `INGESTING`: ingest is currently running.
- `READY`: linked to a normal `Video` row.
- `ERROR`: ingest failed; inspect `ingest_error`.

Batch plugin step statuses:

- `PENDING`: waiting to run.
- `RUNNING`: plugin step is currently running.
- `DONE`: plugin step completed.
- `ERROR`: plugin step failed; inspect `error`.
- `SKIPPED`: step was cancelled before completion.

## Presets

The current production candidate preset is `default_batch_analysis`:

1. `thumbnail`
2. `shotdetection`
3. `shot_type_classification`, using the `shotdetection` shot timeline

Preset validation runs before upload-triggered and manual execution. A preset step may declare fixed parameters and dependency expressions. Dependencies currently support passing timeline ids from completed previous steps.

Plugins that require user-supplied files per run should not be added to a batch preset until they have a batch-safe parameter strategy.

## Operational Limits

Relevant settings:

- `MAX_BATCH_FILES`, default `500`.
- `MAX_BATCH_TOTAL_SIZE`, default `250 GiB`.
- `MAX_BATCH_FILE_SIZE`, optional per-file zip entry limit.
- `MAX_ACTIVE_BATCH_INGESTS_PER_USER`, default `1`.
- `MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH`, default `1`.
- `MAX_ACTIVE_BATCH_PLUGIN_RUNS_GLOBAL`, default `4`.
- `MAX_ACTIVE_BATCH_PLUGIN_RUNS_PER_USER`, default `2`.
- `BATCH_UPLOAD_ROOT`, default `/tmp/video_batches`.

Preset execution uses a scheduler task plus one Celery task per batch plugin step. The scheduler dispatches only dependency-ready steps and applies the per-batch, per-user, and global plugin-run limits before starting more analyser work.

Run `python3 backend/src/backend/manage.py video_batch_cleanup` to remove abandoned temporary batch directories that no longer have matching database rows.

## Docker Smoke Test

Run the API smoke test against a local Docker stack with the backend exposed on `127.0.0.1:8000`:

```powershell
.\scripts\batch-api-smoke.ps1
```

The script logs in as `test@email.com`, copies two existing videos owned by that account into temporary loose-file and zip uploads, verifies ingest/path preservation, runs `default_batch_analysis` against the analyser, retries failed ingest work, cancels a disposable batch, deletes disposable batches, and removes its temporary files.

The local run usually takes under a minute when the backend, Celery worker, analyser, and inference services are already healthy. Use `-VideoPaths` to provide explicit local sample videos when the seeded account has no existing media. Use `-SkipPreset` for a faster ingest-only smoke test when the analyser is intentionally offline.

## Analyser Video Cache

When a plugin uploads a video to the analyser, the returned analyser data id is stored on the `Video` row with the current media file UUID and extension. Later plugins for the same unchanged `Video` reuse that analyser data id and skip the upload call.

Cache lifetime follows the `Video` row and the analyser data store. The cache is invalidated when the `Video` row is deleted. If future code replaces a video's media file in place, it must clear `analyser_data_id`, `analyser_data_file`, and `analyser_data_ext` or update the media file UUID so `Task.upload_video()` treats the cache as stale.

Logs include analyser video cache hit/miss messages from `backend.utils.task`.

## Seeded QA Account

Local QA account:

- Username/email: `test@email.com`
- Password: `password123`
- Seeded batch: `3088719230d94c0fa6bc8623ca353a98`

Remove or rotate this account before production deployment.

## Manual QA Checklist

1. Log in as the seeded QA account.
2. Open `/batches/3088719230d94c0fa6bc8623ca353a98`.
3. Confirm the batch detail table shows seven ready videos and preserved original paths.
4. Open at least one linked video analysis view from the batch.
5. Run `default_batch_analysis`.
6. Confirm plugin chips progress from pending/running to done or a clear error.
7. Upload a zip with nested folders and one unsupported file.
8. Confirm zip-relative paths and unsupported-file errors render clearly.
9. Retry a failed ingest item that still has a source file.
10. Cancel a queued/running batch and confirm `CANCELLED`.
11. Delete a disposable batch and confirm it disappears from the list.
