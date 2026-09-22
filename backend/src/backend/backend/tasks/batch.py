import logging
from pathlib import Path
import time
import zipfile
import uuid
from functools import wraps

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from backend.models import (
    PluginRun,
    Timeline,
    VideoBatch,
    VideoBatchItem,
    VideoBatchPluginRun,
)
from backend.plugin_manager import PluginManager
from backend.utils.batch_upload import (
    extract_zip_videos,
    get_max_active_batch_plugin_runs_global,
    get_max_active_batch_plugin_runs_per_user,
    get_batch_dir,
    get_max_active_plugin_runs_per_batch,
)
from backend.utils.plugin_presets import (
    DEFAULT_BATCH_PRESET,
    build_step_parameters,
    resolve_dependency,
    validate_batch_preset,
    validate_batch_preset_definition,
)
from backend.utils.batch_plugin_catalog import timeline_by_name
from backend.utils.elan_export import (
    ELAN_EXPORT_CACHE_TIMEOUT,
    build_elan_archive,
    elan_export_cache_key,
)
from backend.utils.video_ingest import PathUploadFile, ingest_video_file


logger = logging.getLogger(__name__)


def _update_elan_export_state(job_id, **updates):
    key = elan_export_cache_key(job_id)
    state = cache.get(key)
    if state is None:
        return None
    state.update(updates)
    cache.set(key, state, ELAN_EXPORT_CACHE_TIMEOUT)
    return state


@shared_task
def export_batch_elan(job_id):
    state = _update_elan_export_state(job_id, status="running", phase="exporting")
    if state is None:
        return

    try:
        batch = VideoBatch.objects.get(id=state["batch_id"])
        items = list(
            batch.items.filter(
                ingest_status=VideoBatchItem.STATUS_READY,
                video__isnull=False,
                video__owner=batch.owner,
            )
            .select_related("video")
            .order_by("original_path", "original_filename")
        )

        def progress(processed, total, exported, failed, phase):
            _update_elan_export_state(
                job_id,
                phase=phase,
                processed=processed,
                total=total,
                exported=exported,
                failed=failed,
            )

        report = build_elan_archive(
            items,
            state["archive_path"],
            progress_callback=progress,
        )
        _update_elan_export_state(
            job_id,
            status="complete",
            phase="complete",
            processed=len(items),
            total=len(items),
            exported=len(report["exported"]),
            failed=len(report["failed"]),
        )
    except Exception as exc:
        logger.exception("Failed to build batch ELAN export %s", job_id)
        _update_elan_export_state(
            job_id,
            status="error",
            phase="error",
            error="elan_export_failed",
            error_detail=str(exc),
        )


def batch_processing_paused():
    """Shared durable maintenance switch, checked even by already-running workers."""
    return Path(settings.BATCH_PAUSE_FILE).exists()


def serialized_scheduler(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        # Session lock: claims are committed before publishing to Celery. All batch
        # schedulers share this lock so the global and user limits cannot race.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(74219031)")
        try:
            return function(*args, **kwargs)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(74219031)")
    return wrapped


def serialized_plugin_step(function):
    @wraps(function)
    def wrapped(self, tracker_id, *args, **kwargs):
        # Prevent concurrent/redelivered messages from executing a plugin twice.
        key = uuid.UUID(str(tracker_id)).int & 0x7fffffff
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(74219032, %s)", [key])
            acquired = cursor.fetchone()[0]
        if not acquired:
            return
        try:
            return function(self, tracker_id, *args, **kwargs)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(74219032, %s)", [key])
    return wrapped


def ingest_batch_item(item):
    if item.video_id:
        item.ingest_status = VideoBatchItem.STATUS_READY
        item.ingest_error = ""
        item.save(update_fields=["ingest_status", "ingest_error", "update_date"])
        return

    if not item.source_path:
        item.ingest_status = VideoBatchItem.STATUS_ERROR
        item.ingest_error = "source_file_missing"
        item.save(update_fields=["ingest_status", "ingest_error", "update_date"])
        return

    source_path = Path(item.source_path)
    if not source_path.exists():
        item.ingest_status = VideoBatchItem.STATUS_ERROR
        item.ingest_error = "source_file_missing"
        item.save(update_fields=["ingest_status", "ingest_error", "update_date"])
        return

    if item.checksum:
        duplicate = (
            VideoBatchItem.objects.filter(
                batch=item.batch,
                checksum=item.checksum,
                ingest_status=VideoBatchItem.STATUS_READY,
                video__isnull=False,
            )
            .exclude(id=item.id)
            .first()
        )
        if duplicate:
            item.ingest_status = VideoBatchItem.STATUS_ERROR
            item.ingest_error = "duplicate_in_batch"
            item.save(update_fields=["ingest_status", "ingest_error", "update_date"])
            return

    item.ingest_status = VideoBatchItem.STATUS_INGESTING
    item.ingest_error = ""
    item.save(update_fields=["ingest_status", "ingest_error", "update_date"])

    result = ingest_video_file(
        PathUploadFile(source_path, name=item.original_filename),
        owner=item.batch.owner,
        title=Path(item.original_filename).stem,
        max_size=item.batch.owner.max_video_size,
        source_metadata={"name": Path(item.original_filename).stem},
    )

    if result["status"] == "ok":
        try:
            source_path.unlink()
        except OSError:
            logger.warning("Failed to remove batch source file %s", source_path)
        item.video = result["video"]
        item.ingest_status = VideoBatchItem.STATUS_READY
        item.ingest_error = ""
        item.source_path = ""
        item.save(
            update_fields=[
                "video",
                "source_path",
                "ingest_status",
                "ingest_error",
                "update_date",
            ]
        )
        return

    item.ingest_status = VideoBatchItem.STATUS_ERROR
    item.ingest_error = result.get("type", "ingest_error")
    item.save(update_fields=["ingest_status", "ingest_error", "update_date"])


def create_zip_batch_items(batch):
    extract_dir = get_batch_dir(batch.id) / "extracted"
    try:
        entries = extract_zip_videos(
            batch.source_path,
            extract_dir,
            max_file_size=batch.owner.max_video_size,
        )
    except zipfile.BadZipFile:
        batch.status = VideoBatch.STATUS_ERROR
        batch.save(update_fields=["status", "update_date"])
        VideoBatchItem.objects.create(
            batch=batch,
            original_filename=Path(batch.source_path).name,
            original_path=Path(batch.source_path).name,
            ingest_status=VideoBatchItem.STATUS_ERROR,
            ingest_error="malformed_zip",
        )
        batch.refresh_counters()
        return

    for entry in entries:
        VideoBatchItem.objects.create(
            batch=batch,
            original_filename=entry["original_filename"],
            original_path=entry["original_path"],
            source_path=str(entry.get("source_path", "")),
            file_size=entry.get("file_size", 0),
            checksum=entry.get("checksum", ""),
            ingest_status=(
                VideoBatchItem.STATUS_PENDING
                if entry["status"] == "ok"
                else VideoBatchItem.STATUS_ERROR
            ),
            ingest_error=entry.get("ingest_error", ""),
        )


def get_completed_step_outputs(tracker):
    if tracker.plugin == "shotdetection" and tracker.plugin_run_id:
        timeline = (
            Timeline.objects.filter(
                video=tracker.item.video,
                plugin_run_result__plugin_run=tracker.plugin_run,
            )
            .order_by("order", "id")
            .first()
        )
        if timeline:
            return {"timelines": {"shots": timeline.id.hex}}
    return {}


def is_batch_cancelled(batch):
    batch.refresh_from_db(fields=["status"])
    return batch.status == VideoBatch.STATUS_CANCELLED


def cancel_batch_work(batch):
    VideoBatchItem.objects.filter(
        batch=batch,
        ingest_status__in=[
            VideoBatchItem.STATUS_PENDING,
            VideoBatchItem.STATUS_INGESTING,
        ],
    ).update(
        ingest_status=VideoBatchItem.STATUS_ERROR,
        ingest_error="cancelled",
    )
    VideoBatchPluginRun.objects.filter(
        batch=batch,
        status__in=[
            VideoBatchPluginRun.STATUS_PENDING,
            VideoBatchPluginRun.STATUS_RUNNING,
        ],
    ).update(status=VideoBatchPluginRun.STATUS_SKIPPED, error="cancelled")
    batch.status = VideoBatch.STATUS_CANCELLED
    batch.refresh_counters()


def mark_deleted_video_items(batch):
    deleted_items = VideoBatchItem.objects.filter(
        batch=batch,
        ingest_status=VideoBatchItem.STATUS_READY,
        video__isnull=True,
    )
    if deleted_items.exists():
        deleted_items.update(
            ingest_status=VideoBatchItem.STATUS_ERROR,
            ingest_error="video_deleted",
        )
        VideoBatchPluginRun.objects.filter(
            batch=batch,
            item__video__isnull=True,
            status__in=[
                VideoBatchPluginRun.STATUS_PENDING,
                VideoBatchPluginRun.STATUS_RUNNING,
            ],
        ).update(status=VideoBatchPluginRun.STATUS_SKIPPED, error="video_deleted")
        batch.refresh_counters()


def scoped_ready_items(batch, item_ids=None):
    items = batch.items.filter(
        ingest_status=VideoBatchItem.STATUS_READY,
        video__isnull=False,
    )
    if item_ids is not None:
        items = items.filter(id__in=item_ids)
    return items.order_by("original_path", "original_filename")


def resolve_preset_definition(preset_id=None, preset_definition=None):
    if preset_definition is not None:
        return validate_batch_preset_definition(preset_definition)
    return validate_batch_preset(preset_id)


def ensure_batch_plugin_run_rows(batch, preset_id, preset, item_ids=None):
    existing = set(VideoBatchPluginRun.objects.filter(
        batch=batch, preset=preset_id,
    ).values_list("item_id", "step_index", "plugin"))
    missing = []
    for item_id in scoped_ready_items(batch, item_ids).values_list("id", flat=True):
        for step_index, step in enumerate(preset["steps"]):
            if (item_id, step_index, step["plugin"]) in existing:
                continue
            missing.append(VideoBatchPluginRun(
                batch=batch,
                item_id=item_id,
                preset=preset_id,
                step_index=step_index,
                plugin=step["plugin"],
                status=VideoBatchPluginRun.STATUS_PENDING,
            ))
    VideoBatchPluginRun.objects.bulk_create(missing, batch_size=1000)


def get_running_batch_plugin_counts(batch):
    running = VideoBatchPluginRun.objects.filter(
        status=VideoBatchPluginRun.STATUS_RUNNING
    )
    return {
        "batch": running.filter(batch=batch).count(),
        "user": running.filter(batch__owner=batch.owner).count(),
        "global": running.count(),
    }


def get_step_outputs_for_item(batch, item, preset_id, preset, before_step_index):
    outputs = {}
    for index, step in enumerate(preset["steps"][:before_step_index]):
        tracker = VideoBatchPluginRun.objects.filter(
            batch=batch,
            item=item,
            preset=preset_id,
            step_index=index,
            plugin=step["plugin"],
            status=VideoBatchPluginRun.STATUS_DONE,
        ).first()
        if tracker is None:
            return None
        outputs[step["plugin"]] = get_completed_step_outputs(tracker)
    return outputs


def resolve_step_parameters(step, outputs, item):
    parameters = build_step_parameters(step, outputs)
    if parameters is None:
        return {"status": "skip", "type": "missing_dependency_output"}

    for parameter_name, resolution in step.get("parameter_resolution", {}).items():
        strategy = resolution.get("strategy")
        required = resolution.get("required", True)

        if strategy == "previous_step_output":
            value = resolve_dependency(resolution.get("expression", ""), outputs)
            if value is None and required:
                return {"status": "skip", "type": "missing_dependency_output"}
            if value is not None:
                parameters.append({"name": parameter_name, "value": value})
            continue

        if strategy == "timeline_by_name":
            timeline = timeline_by_name(item.video, resolution.get("name", ""))
            if timeline is None and required:
                return {"status": "skip", "type": "missing_required_timeline"}
            if timeline is not None:
                parameters.append({"name": parameter_name, "value": timeline.id.hex})
            continue

        if strategy == "scalar_timeline_by_name":
            timeline = timeline_by_name(
                item.video,
                resolution.get("name", ""),
                scalar=True,
            )
            if timeline is None and required:
                return {"status": "skip", "type": "missing_required_timeline"}
            if timeline is not None:
                parameters.append({"name": parameter_name, "value": timeline.id.hex})
            continue

        if strategy == "scalar_timelines_by_name":
            names = resolution.get("names", [])
            if not names and required:
                return {"status": "skip", "type": "missing_required_timeline"}
            timelines = []
            for name in names:
                timeline = timeline_by_name(item.video, name, scalar=True)
                if timeline is None and required:
                    return {"status": "skip", "type": "missing_required_timeline"}
                if timeline is not None:
                    timelines.append(timeline.id.hex)
            parameters.append({"name": parameter_name, "value": timelines})
            continue

        if strategy == "shared_file":
            if "path" not in resolution and required:
                return {"status": "error", "type": "shared_input_missing"}
            if "path" in resolution:
                parameters.append({"name": parameter_name, "path": resolution["path"]})
            continue

        return {"status": "error", "type": "unsupported_batch_parameter"}

    return {"status": "ok", "parameters": parameters}


def mark_blocked_dependents(batch, item, preset_id, failed_step_index, error):
    VideoBatchPluginRun.objects.filter(
        batch=batch,
        item=item,
        preset=preset_id,
        step_index__gt=failed_step_index,
        status=VideoBatchPluginRun.STATUS_PENDING,
    ).update(status=VideoBatchPluginRun.STATUS_SKIPPED, error=error)


def next_schedulable_tracker(batch, item, preset_id, preset):
    outputs = {}
    trackers = {
        tracker.step_index: tracker
        for tracker in VideoBatchPluginRun.objects.filter(
            batch=batch,
            item=item,
            preset=preset_id,
        )
    }

    for step_index, step in enumerate(preset["steps"]):
        tracker = trackers.get(step_index)
        if tracker is None:
            return None

        if tracker.status == VideoBatchPluginRun.STATUS_DONE:
            outputs[step["plugin"]] = get_completed_step_outputs(tracker)
            continue

        if tracker.status in {
            VideoBatchPluginRun.STATUS_ERROR,
            VideoBatchPluginRun.STATUS_SKIPPED,
        }:
            mark_blocked_dependents(
                batch,
                item,
                preset_id,
                step_index,
                "dependency_failed",
            )
            return None

        if tracker.status == VideoBatchPluginRun.STATUS_RUNNING:
            return None

        resolved = resolve_step_parameters(step, outputs, item)
        if resolved["status"] != "ok":
            tracker.status = (
                VideoBatchPluginRun.STATUS_SKIPPED
                if resolved["status"] == "skip"
                else VideoBatchPluginRun.STATUS_ERROR
            )
            tracker.error = resolved["type"]
            tracker.save(update_fields=["status", "error", "update_date"])
            mark_blocked_dependents(
                batch,
                item,
                preset_id,
                step_index,
                "dependency_failed",
            )
            return None

        return tracker

    return None


def dispatch_batch_plugin_step(tracker, batch, preset_id, item_ids=None, preset_definition=None):
    if batch_processing_paused():
        return False
    updated = VideoBatchPluginRun.objects.filter(
        id=tracker.id,
        status=VideoBatchPluginRun.STATUS_PENDING,
    ).update(status=VideoBatchPluginRun.STATUS_RUNNING, error="")
    if updated != 1:
        return False

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        run_video_batch_plugin_step(
            tracker.id,
            batch.id,
            preset_id,
            item_ids=item_ids,
            preset_definition=preset_definition,
        )
    else:
        try:
            run_video_batch_plugin_step.apply_async(
                (tracker.id, batch.id, preset_id, item_ids, preset_definition)
            )
        except Exception:
            VideoBatchPluginRun.objects.filter(id=tracker.id, status="R").update(
                status="P", error="dispatch_failed",
            )
            raise
    return True


def finalize_batch_plugin_schedule(batch):
    if batch.plugin_runs.filter(status=VideoBatchPluginRun.STATUS_RUNNING).exists():
        return
    if batch.plugin_runs.filter(status=VideoBatchPluginRun.STATUS_PENDING).exists():
        return

    if batch.plugin_runs.filter(
        status__in=[
            VideoBatchPluginRun.STATUS_ERROR,
            VideoBatchPluginRun.STATUS_SKIPPED,
        ]
    ).exists():
        batch.status = VideoBatch.STATUS_PARTIAL_ERROR
    else:
        batch.refresh_counters(save=False)
        if batch.status != VideoBatch.STATUS_PARTIAL_ERROR:
            batch.status = VideoBatch.STATUS_READY
    batch.save()


@shared_task(bind=True)
def ingest_video_batch(self, batch_id):
    if batch_processing_paused():
        return
    start_time = time.monotonic()
    try:
        batch = VideoBatch.objects.get(id=batch_id)
    except VideoBatch.DoesNotExist:
        logger.error("Video batch %s does not exist", batch_id)
        return

    if batch.status == VideoBatch.STATUS_CANCELLED:
        cancel_batch_work(batch)
        return

    batch.status = VideoBatch.STATUS_INGESTING
    batch.save(update_fields=["status", "update_date"])

    if is_batch_cancelled(batch):
        cancel_batch_work(batch)
        return

    if batch.source_type == VideoBatch.SOURCE_ZIP and batch.items.count() == 0:
        create_zip_batch_items(batch)

    if is_batch_cancelled(batch):
        cancel_batch_work(batch)
        return

    for item in batch.items.filter(
        ingest_status__in=[
            VideoBatchItem.STATUS_PENDING,
            VideoBatchItem.STATUS_ERROR,
        ]
    ).order_by("date"):
        if batch_processing_paused():
            return
        if is_batch_cancelled(batch):
            cancel_batch_work(batch)
            return

        if item.ingest_status == VideoBatchItem.STATUS_ERROR and not item.source_path:
            continue
        try:
            ingest_batch_item(item)
        except Exception:
            logger.exception("Failed to ingest batch item %s", item.id)
            item.ingest_status = VideoBatchItem.STATUS_ERROR
            item.ingest_error = "ingest_error"
            item.save(update_fields=["ingest_status", "ingest_error", "update_date"])
        finally:
            if is_batch_cancelled(batch):
                cancel_batch_work(batch)
                return
            batch.refresh_counters()

    if is_batch_cancelled(batch):
        cancel_batch_work(batch)
        return

    batch.refresh_counters()
    logger.info(
        "batch_ingest_complete batch_id=%s status=%s total=%s ready=%s failed=%s duration_seconds=%.3f",
        batch.id,
        batch.STATUS[batch.status],
        batch.total_count,
        batch.ready_count,
        batch.failed_count,
        time.monotonic() - start_time,
    )
    if batch.status == VideoBatch.STATUS_READY and batch.source_path:
        try:
            Path(batch.source_path).unlink()
        except OSError:
            logger.warning("Failed to remove batch source archive %s", batch.source_path)
        batch.source_path = ""
        batch.save(update_fields=["source_path", "update_date"])
    if batch.auto_run_preset and batch.preset and batch.ready_count > 0:
        run_video_batch_preset.apply_async((batch.id, batch.preset))


@shared_task(bind=True)
@serialized_scheduler
def run_video_batch_preset(
    self,
    batch_id,
    preset_id=None,
    item_ids=None,
    preset_definition=None,
):
    if batch_processing_paused():
        return
    scheduler_start_time = time.monotonic()
    try:
        batch = VideoBatch.objects.get(id=batch_id)
    except VideoBatch.DoesNotExist:
        logger.error("Video batch %s does not exist", batch_id)
        return

    if preset_definition is None and preset_id == batch.preset:
        preset_definition = batch.custom_preset_definition
    if item_ids is None and preset_definition is not None:
        item_ids = batch.custom_preset_item_ids

    validation = resolve_preset_definition(preset_id, preset_definition)
    if validation["status"] != "ok":
        batch.status = VideoBatch.STATUS_ERROR
        batch.save(update_fields=["status", "update_date"])
        logger.error("Invalid batch preset %s: %s", preset_id, validation)
        return

    preset = validation["preset"]
    preset_id = preset_id or batch.preset or DEFAULT_BATCH_PRESET
    if get_max_active_plugin_runs_per_batch() < 1:
        batch.status = VideoBatch.STATUS_ERROR
        batch.save(update_fields=["status", "update_date"])
        logger.error("MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH must be at least 1")
        return

    if batch.status == VideoBatch.STATUS_CANCELLED:
        cancel_batch_work(batch)
        return

    mark_deleted_video_items(batch)
    batch.status = VideoBatch.STATUS_RUNNING
    batch.preset = preset_id
    batch.save(update_fields=["status", "preset", "update_date"])
    ensure_batch_plugin_run_rows(batch, preset_id, preset, item_ids=item_ids)

    dispatched_count = 0
    while True:
        if is_batch_cancelled(batch):
            cancel_batch_work(batch)
            return

        counts = get_running_batch_plugin_counts(batch)
        capacity = min(
            get_max_active_plugin_runs_per_batch() - counts["batch"],
            get_max_active_batch_plugin_runs_per_user() - counts["user"],
            get_max_active_batch_plugin_runs_global() - counts["global"],
        )
        if capacity <= 0:
            break

        dispatched = False
        items = scoped_ready_items(batch, item_ids)
        lowest_unfinished_step = (
            VideoBatchPluginRun.objects.filter(
                batch=batch,
                item_id__in=items.values("id"),
                preset=preset_id,
                status__in=[
                    VideoBatchPluginRun.STATUS_PENDING,
                    VideoBatchPluginRun.STATUS_RUNNING,
                ],
            )
            .order_by("step_index")
            .values_list("step_index", flat=True)
            .first()
        )

        candidates = VideoBatchPluginRun.objects.filter(
            batch=batch, preset=preset_id, item_id__in=items.values("id"),
            step_index=lowest_unfinished_step, status=VideoBatchPluginRun.STATUS_PENDING,
        ).select_related("item", "item__video").order_by("item__original_path", "id")
        # Inspect only enough candidates to fill the available slots. Do not
        # rescan every video and every completed output after each completion.
        examined = False
        for candidate in candidates[:max(capacity, 32)]:
            examined = True
            tracker = next_schedulable_tracker(batch, candidate.item, preset_id, preset)
            if tracker is None:
                continue

            tracker_dispatched = dispatch_batch_plugin_step(
                tracker,
                batch,
                preset_id,
                item_ids=item_ids,
                preset_definition=preset_definition,
            )
            if tracker_dispatched:
                dispatched = True
                dispatched_count += 1
                capacity -= 1
            if capacity <= 0:
                break

        if batch_processing_paused() or (not dispatched and not examined):
            break

    if is_batch_cancelled(batch):
        cancel_batch_work(batch)
        return

    finalize_batch_plugin_schedule(batch)
    logger.info(
        "batch_scheduler_tick batch_id=%s preset=%s dispatched=%s duration_seconds=%.3f",
        batch.id,
        preset_id,
        dispatched_count,
        time.monotonic() - scheduler_start_time,
    )


@shared_task(bind=True)
@serialized_plugin_step
def run_video_batch_plugin_step(
    self,
    tracker_id,
    batch_id,
    preset_id,
    item_ids=None,
    preset_definition=None,
):
    step_start_time = time.monotonic()
    try:
        tracker = VideoBatchPluginRun.objects.select_related(
            "batch",
            "item",
            "item__video",
            "batch__owner",
        ).get(id=tracker_id, batch_id=batch_id)
    except VideoBatchPluginRun.DoesNotExist:
        logger.error("Video batch plugin step %s does not exist", tracker_id)
        return

    batch = tracker.batch
    if batch_processing_paused():
        return
    if tracker.status != VideoBatchPluginRun.STATUS_RUNNING:
        return
    if batch.status == VideoBatch.STATUS_CANCELLED:
        cancel_batch_work(batch)
        return

    if tracker.item.video is None:
        tracker.status = VideoBatchPluginRun.STATUS_SKIPPED
        tracker.error = "video_deleted"
        tracker.save(update_fields=["status", "error", "update_date"])
        mark_deleted_video_items(batch)
        run_video_batch_preset.apply_async(
            (batch.id, preset_id, item_ids, preset_definition)
        )
        return

    validation = resolve_preset_definition(preset_id, preset_definition)
    if validation["status"] != "ok":
        tracker.status = VideoBatchPluginRun.STATUS_ERROR
        tracker.error = validation.get("type", "invalid_preset")
        tracker.save(update_fields=["status", "error", "update_date"])
        run_video_batch_preset.apply_async(
            (batch.id, preset_id, item_ids, preset_definition)
        )
        return

    preset = validation["preset"]
    step = preset["steps"][tracker.step_index]
    queue_wait_seconds = (timezone.now() - tracker.date).total_seconds()
    outputs = get_step_outputs_for_item(
        batch,
        tracker.item,
        preset_id,
        preset,
        tracker.step_index,
    )
    resolved = (
        {"status": "skip", "type": "missing_dependency_output"}
        if outputs is None
        else resolve_step_parameters(step, outputs, tracker.item)
    )
    if resolved["status"] != "ok":
        tracker.status = (
            VideoBatchPluginRun.STATUS_SKIPPED
            if resolved["status"] == "skip"
            else VideoBatchPluginRun.STATUS_ERROR
        )
        tracker.error = resolved["type"]
        tracker.save(update_fields=["status", "error", "update_date"])
        run_video_batch_preset.apply_async(
            (batch.id, preset_id, item_ids, preset_definition)
        )
        return
    parameters = resolved["parameters"]

    plugin_manager = PluginManager()
    def link_plugin_run(plugin_run):
        VideoBatchPluginRun.objects.filter(id=tracker.id).update(plugin_run=plugin_run)

    try:
        result = plugin_manager(
            step["plugin"],
            user=batch.owner,
            video=tracker.item.video,
            run_async=False,
            parameters=parameters,
            on_created=link_plugin_run,
        )
    except Exception:
        logger.exception("Unexpected batch plugin error tracker_id=%s", tracker.id)
        tracker.refresh_from_db(fields=["plugin_run"])
        result = {"status": False, "type": "plugin_run_failed"}
    plugin_run_id = result.get("plugin_run")
    if plugin_run_id:
        try:
            tracker.plugin_run = PluginRun.objects.get(id=plugin_run_id)
        except PluginRun.DoesNotExist:
            tracker.plugin_run = None

    if is_batch_cancelled(batch):
        tracker.status = VideoBatchPluginRun.STATUS_SKIPPED
        tracker.error = "cancelled"
        tracker.save(update_fields=["plugin_run", "status", "error", "update_date"])
        cancel_batch_work(batch)
        return

    if not result.get("status"):
        tracker.status = VideoBatchPluginRun.STATUS_ERROR
        tracker.error = result.get("type") or result.get("error") or "plugin_run_failed"
    else:
        tracker.status = VideoBatchPluginRun.STATUS_DONE
        tracker.error = ""
    tracker.save(update_fields=["plugin_run", "status", "error", "update_date"])
    logger.info(
        "batch_plugin_step_complete batch_id=%s item_id=%s tracker_id=%s plugin=%s status=%s queue_wait_seconds=%.3f duration_seconds=%.3f",
        batch.id,
        tracker.item.id,
        tracker.id,
        tracker.plugin,
        tracker.STATUS[tracker.status],
        queue_wait_seconds,
        time.monotonic() - step_start_time,
    )
    run_video_batch_preset.apply_async((batch.id, preset_id, item_ids, preset_definition))
