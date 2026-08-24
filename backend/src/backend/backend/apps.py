import logging
import sys

from django.apps import AppConfig
from django.db.models import Q
from django.db import connection


logger = logging.getLogger(__name__)


class BackendConfig(AppConfig):
    name = "backend"

    def ready(self):
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if sys.argv[0].endswith("manage.py") and command not in {"runserver"}:
            return

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

        celery_runs = []
        celery_batch_runs = []
        celery_categories = (
            list(scheduled.values()) + list(active.values()) + list(reserved.values())
        )
        for category in celery_categories:
            for run in category:
                args = run.get("args") or []
                if not args:
                    continue

                if run.get("name") == "backend.plugin_manager.run_plugin":
                    first_arg = args[0]
                    if isinstance(first_arg, dict) and first_arg.get("plugin_run"):
                        celery_runs.append(first_arg["plugin_run"])
                    continue

                if run.get("name") in {
                    "backend.tasks.batch.ingest_video_batch",
                    "backend.tasks.batch.run_video_batch_preset",
                }:
                    celery_batch_runs.append(args[0])
                    continue

                if run.get("name") == "backend.tasks.batch.run_video_batch_plugin_step":
                    if len(args) > 1:
                        celery_batch_runs.append(args[1])

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
        ).exclude(batch_id__in=celery_batch_runs)
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
        ).exclude(batch_id__in=celery_batch_runs)
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
        ).exclude(id__in=celery_batch_runs)
        if interrupted_batches.exists():
            logger.warning(
                f'Setting {interrupted_batches.count()} interrupted batches to PARTIAL_ERROR'
            )
            interrupted_batches.update(status=VideoBatch.STATUS_PARTIAL_ERROR)
