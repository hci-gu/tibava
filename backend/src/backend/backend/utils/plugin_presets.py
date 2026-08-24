import copy

from backend.plugin_manager import PluginManager


DEFAULT_BATCH_PRESET = "default_batch_analysis"

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
    return [
        {
            "id": preset_id,
            "name": preset["name"],
            "description": preset.get("description", ""),
            "steps": preset["steps"],
        }
        for preset_id, preset in BATCH_PLUGIN_PRESETS.items()
    ]


def get_batch_preset(preset_id=None):
    preset_id = preset_id or DEFAULT_BATCH_PRESET
    return BATCH_PLUGIN_PRESETS.get(preset_id)


def validate_batch_preset(preset_id=None):
    preset = get_batch_preset(preset_id)
    if preset is None:
        return {"status": "error", "type": "not_exist"}

    plugin_manager = PluginManager()
    for step in preset["steps"]:
        plugin = step["plugin"]
        if plugin not in plugin_manager:
            return {"status": "error", "type": "plugin_not_exist", "plugin": plugin}

        parser = plugin_manager._parser.get(plugin)
        if parser is not None and parser()(copy.deepcopy(step.get("parameters", []))) is None:
            return {
                "status": "error",
                "type": "invalid_plugin_parameters",
                "plugin": plugin,
            }

    return {"status": "ok", "preset": preset}


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
