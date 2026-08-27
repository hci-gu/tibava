# Batch Plugin Catalog

Batch plugin execution is driven by `backend.utils.batch_plugin_catalog`.

Each catalog entry describes the plugin shown to users and the batch adapter rules used by validation:

- `plugin`: backend plugin id.
- `name`, `description`, `group`, `icon`: UI display metadata.
- `parameters`: required parameter controls and default values.
- `optional_parameters`: optional parameter controls and default values.
- `batch.supported`: whether the plugin can be selected in the batch custom-set UI.
- `batch.unsupported_reason`: reason shown when a plugin is disabled.
- `batch.parameter_strategies`: supported batch resolution modes for non-shared parameters.
- `batch.outputs`: output types a later plugin can depend on.
- `batch.allow_duplicate`: whether the same plugin can appear more than once in a custom set.
- `batch.file_inputs`: `unsupported`, `shared`, or a future plugin-specific mode.

## Parameter Resolution

The custom plugin-set payload supports static shared parameters plus deferred per-video resolution:

- `previous_step_output`: resolves a value from outputs produced by an earlier plugin step.
- `timeline_by_name`: finds a timeline with the requested name on each video.
- `scalar_timeline_by_name`: finds a scalar timeline with the requested name on each video.
- `scalar_timelines_by_name`: finds multiple scalar timelines by name on each video.
- `shared_file`: uploads one batch-scoped file and reuses the stored path for every selected video.

Validation rejects unsupported plugins and invalid dependency graphs before scheduling. Per-video timeline misses become skipped videos where possible instead of failing the whole batch.

## Compatibility Status

The catalog includes every plugin exposed by the current single-video plugin modal.

Currently enabled plugin categories:

- Simple shared-parameter plugins: audio analysis, color analysis, CLIP text-query plugins, OCR, face clustering, thumbnail, text plugins, and other plugins whose parameters can be reused across every selected video.
- Dependency-capable plugins: shot-based classifiers can use either a previous `shotdetection` output or timeline-by-name resolution.
- Scalar timeline plugins: scalar timeline parameters are represented with by-name resolution and preflight validation.
- Shared file plugins: face identification and CLIP ontology can reuse one uploaded image or CSV input across every selected video.
- Multi-scalar plugins: aggregate scalar maps each requested scalar timeline name per video before dispatch.

Currently disabled plugin categories:

- Plugins that are not registered in the backend runtime are shown disabled with a runtime-generated reason.

Do not mark a plugin `batch.supported=true` until its parameter strategy is explicit and covered by backend validation.
