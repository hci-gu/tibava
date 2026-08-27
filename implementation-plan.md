# Run Any Plugin In Batch Implementation Plan

## Goal

Allow users to run any plugin in a video batch when that plugin has an explicit, validated batch execution strategy. The batch flow must support selected videos, folders, filtered subsets, shared parameters, plugin dependencies, per-video timeline resolution, clear validation errors, and partial skips without creating large failed jobs.

## 1. Plugin Metadata And Catalog

- [x] Create a shared plugin catalog module used by both single-video and batch plugin UIs.
- [x] Move plugin name, group, icon, description, required parameters, and optional parameters out of `ModalPlugin.vue`.
- [x] Add batch-specific metadata for each plugin.
- [x] Add `batch_supported` to indicate whether a plugin can currently run in batch mode.
- [x] Add `batch_unsupported_reason` for plugins that should appear disabled.
- [x] Add default batch-safe parameter values where applicable.
- [x] Add metadata for expected plugin output types.
- [x] Add metadata for whether duplicate runs of the same plugin are allowed.
- [x] Add metadata for whether file inputs are allowed, shared, or unsupported in batch mode.

## 2. Batch Compatibility Classification

- [x] Audit every plugin currently exposed in the single-video plugin modal.
- [x] Classify plugins that are batch-safe with no special handling.
- [x] Classify plugins that are batch-safe with shared default parameters.
- [x] Classify plugins that are batch-safe only when an earlier step provides an output dependency.
- [x] Classify plugins that need timeline-by-name or scalar-timeline-by-name mapping per video.
- [x] Classify plugins that need shared uploaded inputs, such as query images.
- [x] Classify plugins that are not batch-safe yet.
- [x] Document the compatibility status and reason for every plugin.

## 3. Parameter Strategy Support

- [x] Support shared batch values for text fields.
- [x] Support shared batch values for sliders.
- [x] Support shared batch values for select options.
- [x] Support shared batch values for button groups.
- [x] Support shared uploaded file parameters where the backend can safely reuse one input for every video.
- [x] Add explicit unsupported handling for file inputs that cannot be reused safely.
- [x] Support `select_timeline` parameters from previous plugin-step outputs.
- [x] Support `select_timeline` parameters by matching timeline name per video.
- [x] Support `select_scalar_timeline` parameters by matching scalar timeline name per video.
- [x] Support multi-timeline parameters where each selected timeline can be mapped per video.
- [x] Add plugin-specific parameter adapters only where generic handling is not sufficient.

## 4. Backend Custom Preset Format

- [x] Extend the custom plugin-set payload to support parameter resolution strategies.
- [x] Add `previous_step_output` resolution for dependency parameters.
- [x] Add `timeline_by_name` resolution for timeline parameters.
- [x] Add `scalar_timeline_by_name` resolution for scalar timeline parameters.
- [x] Add `shared_file` resolution for reusable uploaded input parameters.
- [x] Preserve the current simple `parameters` and `dependencies` shape for existing batch-safe plugins.
- [x] Generate stable custom preset ids from the complete normalized custom preset definition.
- [x] Ensure custom preset definitions can be passed through Celery scheduler retries and continuations.

## 5. Backend Validation And Preflight

- [x] Add `GET /api/video/batch/plugin-catalog`.
- [x] Add `POST /api/video/batch/validate-plugin-set`.
- [x] Validate that every requested plugin exists.
- [x] Validate that every requested plugin is batch-supported.
- [x] Validate required shared parameters before scheduling.
- [x] Validate parser compatibility after resolving default and shared parameter values.
- [x] Validate dependency graph ordering and reject cycles.
- [x] Validate that previous-step output references point to earlier steps.
- [x] Validate selected batch scope has at least one ready video.
- [x] Preflight per-video timeline and scalar timeline mappings.
- [x] Return a summary of runnable videos, skipped videos, skipped reasons, and total jobs.
- [x] Keep `POST /api/video/batch/run-plugin-set` aligned with the validation endpoint.

## 6. Scheduler And Execution Semantics

- [x] Resolve batch parameters per `VideoBatchItem` immediately before dispatch.
- [x] Skip only affected videos when per-video inputs are missing.
- [x] Continue running valid videos when other videos are skipped.
- [x] Store clear skip/error reasons on `VideoBatchPluginRun`.
- [x] Support `missing_required_timeline` as a plugin-step skip reason.
- [x] Support `missing_dependency_output` as a plugin-step skip reason.
- [x] Support `unsupported_batch_parameter` as a validation error.
- [x] Support `invalid_parameters` as a validation or plugin-step error.
- [x] Support `shared_input_missing` as a validation error.
- [x] Support `plugin_not_batch_supported` as a validation error.
- [x] Keep retry behavior compatible with scoped custom plugin sets.
- [x] Keep cancellation behavior compatible with scoped custom plugin sets.
- [x] Keep startup recovery compatible with custom plugin-set scheduler tasks.

## 7. Batch Custom Plugin Set UI

- [x] Replace the temporary three-plugin list with the shared plugin catalog.
- [x] Render plugins grouped by category.
- [x] Add plugin search.
- [x] Show unsupported plugins disabled with their unsupported reason.
- [x] Allow adding supported plugins to a selected step sequence.
- [x] Allow removing plugins from the sequence.
- [x] Allow reordering plugins in the sequence.
- [x] Show dependency warnings while ordering plugins.
- [x] Show parameter editors for each selected step.
- [x] Show timeline dependency selectors for plugins that need prior outputs.
- [x] Show timeline-by-name selectors for plugins that need existing timelines.
- [x] Show shared file inputs only for plugins that support shared files in batch mode.
- [x] Disable final run until validation passes.
- [x] Show validation/preflight results before running.
- [x] Show selected scope summary before running.
- [x] Show estimated job count before running.

## 8. Batch Scope UI

- [x] Keep support for running on all ready videos.
- [x] Keep support for running on manually selected videos.
- [x] Keep support for running on the current folder.
- [x] Keep support for running on current folder and subfolders.
- [x] Keep support for running on filtered results.
- [x] Include skipped video counts in the run confirmation.
- [x] Include plugin-step count and total job count in the run confirmation.

## 9. Plugin Rollout Order

- [x] Enable all simple shared-parameter plugins first.
- [x] Enable dependency-based plugins after previous-step output resolution is generalized.
- [x] Enable timeline-by-name plugins after per-video preflight is in place.
- [x] Enable scalar-timeline-by-name plugins after scalar mapping is in place.
- [x] Enable shared-file plugins after upload/reuse handling is implemented.
- [x] Leave plugins disabled until their batch adapter and validation strategy are explicit.

## 10. Tests

- [x] Add backend tests for catalog endpoint output.
- [x] Add backend tests for validation endpoint success with simple shared-parameter plugins.
- [x] Add backend tests for unsupported plugin rejection.
- [x] Add backend tests for missing required parameter rejection.
- [x] Add backend tests for dependency cycle rejection.
- [x] Add backend tests for previous-step output dependency resolution.
- [x] Add backend tests for timeline-by-name resolution.
- [x] Add backend tests for missing timeline per-video skips.
- [x] Add backend tests for shared file parameter handling.
- [x] Add backend tests for selected-video scoped custom runs.
- [x] Add backend tests for folder-scoped custom runs.
- [x] Add backend tests for retrying scoped custom runs.
- [x] Add frontend build verification.
- [x] Add browser smoke for plugin catalog rendering.
- [x] Add browser smoke for disabled unsupported plugins.
- [x] Add browser smoke for adding, removing, and reordering plugin steps.
- [x] Add browser smoke for validation summary display.
- [x] Add browser smoke for selected-video custom run.
- [x] Add browser smoke for folder custom run.

## 11. Documentation And Release Readiness

- [x] Document the batch plugin metadata schema.
- [x] Document how to mark a plugin batch-supported.
- [x] Document parameter resolution strategies.
- [x] Document known unsupported plugin categories.
- [x] Document operator expectations for partial skips.
- [x] Run backend compile.
- [x] Run full backend tests.
- [x] Run frontend build.
- [x] Run Docker API smoke.
- [x] Run browser smoke for batch upload, selection, folder runs, custom plugin sets, and batch deletion.
