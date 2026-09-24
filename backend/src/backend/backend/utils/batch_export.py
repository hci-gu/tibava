"""Helpers for selecting fully processed batch items for export."""

from backend.utils.plugin_presets import validate_batch_preset


def items_with_completed_preset(batch, items):
    """Return items whose steps in the batch's selected preset are all DONE."""
    item_list = list(items)
    preset_id = batch.preset
    if not preset_id:
        # Batches without a selected preset have no expected plugin steps.
        return item_list

    if batch.custom_preset_definition is not None:
        preset = batch.custom_preset_definition
    else:
        result = validate_batch_preset(preset_id)
        if result.get("status") != "ok":
            return []
        preset = result["preset"]

    steps = preset.get("steps", [])
    if not steps:
        return []

    from backend.models import VideoBatchPluginRun

    done_steps = set(
        VideoBatchPluginRun.objects.filter(
            batch=batch,
            preset=preset_id,
            status=VideoBatchPluginRun.STATUS_DONE,
            item_id__in=[item.id for item in item_list],
        ).values_list("item_id", "step_index", "plugin")
    )
    expected_steps = [
        (index, step["plugin"]) for index, step in enumerate(steps)
    ]
    return [
        item
        for item in item_list
        if all(
            (item.id, step_index, plugin) in done_steps
            for step_index, plugin in expected_steps
        )
    ]
