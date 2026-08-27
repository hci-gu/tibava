import copy
import hashlib
import json

from backend.plugin_manager import PluginManager


DEFAULT_BATCH_PRESET = "default_batch_analysis"
CUSTOM_BATCH_PRESET_PREFIX = "custom:"

BATCH_PLUGIN_PRESETS = {
    DEFAULT_BATCH_PRESET: {
        "name": "Default batch analysis",
        "description": (
            "Generate thumbnails, detect shots, and classify camera setting "
            "for each ready video."
        ),
        "steps": [
            {
                "plugin": "thumbnail",
                "parameters": [],
            },
            {
                "plugin": "shotdetection",
                "parameters": [
                    {"name": "timeline", "value": "Shots"},
                    {"name": "fps", "value": 2.0},
                ],
            },
            {
                "plugin": "shot_type_classification",
                "parameters": [
                    {"name": "timeline", "value": "Camera Setting"},
                    {"name": "fps", "value": 2.0},
                ],
                "dependencies": {
                    "shot_timeline_id": "shotdetection.timelines.shots",
                },
            },
        ],
    }
}


def list_batch_presets():
    presets = []
    for preset_id, preset in BATCH_PLUGIN_PRESETS.items():
        if validate_batch_preset(preset_id)["status"] != "ok":
            continue
        presets.append(
            {
                "id": preset_id,
                "name": preset["name"],
                "description": preset.get("description", ""),
                "steps": preset["steps"],
            }
        )
    return presets


def get_batch_preset(preset_id=None):
    preset_id = preset_id or DEFAULT_BATCH_PRESET
    return BATCH_PLUGIN_PRESETS.get(preset_id)


def make_custom_batch_preset_id(preset):
    payload = json.dumps(preset.get("steps", []), sort_keys=True, separators=(",", ":"))
    return f"{CUSTOM_BATCH_PRESET_PREFIX}{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def normalize_custom_batch_preset(steps, name="Custom batch analysis"):
    from backend.utils.batch_plugin_catalog import batch_metadata_for_plugin

    if not isinstance(steps, list) or not steps:
        return {"status": "error", "type": "empty_preset"}

    normalized_steps = []
    seen_plugins = set()
    for step in steps:
        if not isinstance(step, dict):
            return {"status": "error", "type": "invalid_preset_step"}

        plugin = step.get("plugin")
        if not isinstance(plugin, str) or not plugin:
            return {"status": "error", "type": "missing_plugin"}
        if plugin in seen_plugins:
            return {"status": "error", "type": "duplicate_plugin", "plugin": plugin}
        seen_plugins.add(plugin)

        metadata = batch_metadata_for_plugin(plugin)
        if metadata is None:
            return {"status": "error", "type": "plugin_not_exist", "plugin": plugin}
        if not metadata["batch"]["supported"]:
            return {
                "status": "error",
                "type": "plugin_not_batch_supported",
                "plugin": plugin,
                "reason": metadata["batch"].get("unsupported_reason", ""),
            }

        parameters = step.get("parameters", [])
        dependencies = step.get("dependencies", {})
        parameter_resolution = step.get("parameter_resolution", {})
        if (
            not isinstance(parameters, list)
            or not isinstance(dependencies, dict)
            or not isinstance(parameter_resolution, dict)
        ):
            return {"status": "error", "type": "invalid_preset_step", "plugin": plugin}

        normalized_steps.append(
            {
                "plugin": plugin,
                "parameters": [
                    {"name": parameter.get("name"), "value": parameter.get("value")}
                    for parameter in parameters
                    if (
                        isinstance(parameter, dict)
                        and parameter.get("name")
                        and parameter.get("value") is not None
                    )
                ],
                "dependencies": {
                    key: value
                    for key, value in dependencies.items()
                    if isinstance(key, str) and isinstance(value, str)
                },
                "parameter_resolution": {
                    key: value
                    for key, value in parameter_resolution.items()
                    if isinstance(key, str) and isinstance(value, dict)
                },
            }
        )

    preset = {
        "name": name,
        "description": "Custom plugin set",
        "steps": normalized_steps,
    }
    validation = validate_batch_preset_definition(preset)
    if validation["status"] != "ok":
        return validation
    return {
        "status": "ok",
        "preset": preset,
        "preset_id": make_custom_batch_preset_id(preset),
    }


def validate_batch_preset_definition(preset):
    if preset is None:
        return {"status": "error", "type": "not_exist"}
    steps = preset.get("steps", [])
    if not steps:
        return {"status": "error", "type": "empty_preset"}

    plugin_manager = PluginManager()
    previous_plugins = set()
    current_plugins = {step.get("plugin") for step in steps}
    for step in steps:
        plugin = step["plugin"]
        if plugin not in plugin_manager:
            return {"status": "error", "type": "plugin_not_exist", "plugin": plugin}

        for parameter_name, expression in step.get("dependencies", {}).items():
            dependency_plugin = expression.split(".", 1)[0]
            if dependency_plugin not in current_plugins:
                return {
                    "status": "error",
                    "type": "invalid_dependency",
                    "plugin": plugin,
                    "parameter": parameter_name,
                    "dependency": expression,
                }
            if dependency_plugin not in previous_plugins:
                return {
                    "status": "error",
                    "type": "dependency_cycle",
                    "plugin": plugin,
                    "parameter": parameter_name,
                    "dependency": expression,
                }

        for parameter_name, resolution in step.get("parameter_resolution", {}).items():
            if resolution.get("strategy") != "previous_step_output":
                continue
            dependency_plugin = resolution.get("expression", "").split(".", 1)[0]
            if dependency_plugin not in current_plugins:
                return {
                    "status": "error",
                    "type": "invalid_dependency",
                    "plugin": plugin,
                    "parameter": parameter_name,
                    "dependency": resolution.get("expression", ""),
                }
            if dependency_plugin not in previous_plugins:
                return {
                    "status": "error",
                    "type": "dependency_cycle",
                    "plugin": plugin,
                    "parameter": parameter_name,
                    "dependency": resolution.get("expression", ""),
                }

        parser = plugin_manager._parser.get(plugin)
        if parser is not None and parser()(
            validation_parameters_for_step(step)
        ) is None:
            return {
                "status": "error",
                "type": "invalid_parameters",
                "plugin": plugin,
            }
        previous_plugins.add(plugin)

    return {"status": "ok", "preset": preset}


def validate_batch_preset(preset_id=None):
    return validate_batch_preset_definition(get_batch_preset(preset_id))


def validation_parameters_for_step(step):
    parameters = copy.deepcopy(step.get("parameters", []))
    existing_names = {parameter.get("name") for parameter in parameters}
    deferred_names = set(step.get("dependencies", {}).keys())
    deferred_names.update(step.get("parameter_resolution", {}).keys())

    for parameter_name in deferred_names:
        if parameter_name in existing_names:
            continue
        value = ["deferred"] if parameter_name.endswith("ids") else "deferred"
        parameters.append({"name": parameter_name, "value": value})
    return parameters


def resolve_dependency(expression, outputs):
    parts = expression.split(".")
    current = outputs
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def build_step_parameters(step, outputs):
    parameters = copy.deepcopy(step.get("parameters", []))
    for parameter_name, expression in step.get("dependencies", {}).items():
        value = resolve_dependency(expression, outputs)
        if value is None:
            return None
        parameters.append({"name": parameter_name, "value": value})
    return parameters
