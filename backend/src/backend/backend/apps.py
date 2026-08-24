import logging

from django.apps import AppConfig
from django.db.models import Q
from django.db import connection


logger = logging.getLogger(__name__)


class BackendConfig(AppConfig):
    name = "backend"

    def ready(self):
        table_names = connection.introspection.table_names()
        if 'backend_pluginrun' not in table_names:
            return
        # import here otherwise django complains
        from tibava.celery import app
        from backend.models import PluginRun

        # set unfinished tasks to ERROR on startup
        inspect = app.control.inspect()

        scheduled = inspect.scheduled()
        active = inspect.active()
        reserved = inspect.reserved()

        # inspect can return None or dict
        if scheduled is None or active is None or reserved is None:
            return

        celery_runs = [
            run['args'][0]['plugin_run']
            for category in (list(scheduled.values()) +
                             list(active.values()) +
                             list(reserved.values()))
            for run in category
        ]

        open_runs = PluginRun.objects.exclude(Q(status=PluginRun.STATUS_DONE)|
                                              Q(status=PluginRun.STATUS_ERROR)|
                                              Q(id__in=celery_runs))
        if len(open_runs) > 0:
            logger.warning(
                f'Setting the status of {len(open_runs)} non-running PluginRuns to UNKNOWN'
            )
            open_runs.update(status=PluginRun.STATUS_UNKNOWN)

        if 'backend_videobatch' not in table_names:
            return

        from backend.models import VideoBatch, VideoBatchItem, VideoBatchPluginRun

        interrupted_items = VideoBatchItem.objects.filter(
            ingest_status=VideoBatchItem.STATUS_INGESTING
        )
        if interrupted_items.exists():
            logger.warning(
                f'Setting {interrupted_items.count()} interrupted batch items to ERROR'
            )
            interrupted_items.update(
                ingest_status=VideoBatchItem.STATUS_ERROR,
                ingest_error='interrupted',
            )

        interrupted_plugin_steps = VideoBatchPluginRun.objects.filter(
            status=VideoBatchPluginRun.STATUS_RUNNING
        )
        if interrupted_plugin_steps.exists():
            logger.warning(
                f'Setting {interrupted_plugin_steps.count()} interrupted batch plugin steps to ERROR'
            )
            interrupted_plugin_steps.update(
                status=VideoBatchPluginRun.STATUS_ERROR,
                error='interrupted',
            )

        interrupted_batches = VideoBatch.objects.filter(
            status__in=[
                VideoBatch.STATUS_UPLOADING,
                VideoBatch.STATUS_INGESTING,
                VideoBatch.STATUS_RUNNING,
            ]
        )
        if interrupted_batches.exists():
            logger.warning(
                f'Setting {interrupted_batches.count()} interrupted batches to PARTIAL_ERROR'
            )
            interrupted_batches.update(status=VideoBatch.STATUS_PARTIAL_ERROR)
