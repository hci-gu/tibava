"""Persistent spatial records: normalized decoded-frame xywh, time in seconds."""

from django.conf import settings
from data import Annotation, DataManager
from backend.models import PluginRunResult


def save_spatial_result(plugin_run, name, records, manager=None):
    manager = manager or DataManager(settings.DATA_OUTPUT_PATH)
    with manager.create_data("AnnotationData") as data:
        for record in records:
            record = dict(record)
            start = float(record.pop("time"))
            end = start + float(record.pop("delta_time"))
            data.annotations.append(Annotation(start=start, end=end, labels=[record]))
    return PluginRunResult.objects.create(
        plugin_run=plugin_run, name=name, type=PluginRunResult.TYPE_SPATIAL, data_id=data.id,
    )
