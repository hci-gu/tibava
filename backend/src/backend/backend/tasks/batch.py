import logging
from pathlib import Path
import zipfile

from celery import shared_task

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
    get_batch_dir,
    get_max_active_plugin_runs_per_batch,
)
from backend.utils.plugin_presets import (
    DEFAULT_BATCH_PRESET,
    build_step_parameters,
    validate_batch_preset,
)
from backend.utils.video_ingest import PathUploadFile, ingest_video_file


logger = logging.getLogger(__name__)


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


@shared_task(bind=True)
def ingest_video_batch(self, batch_id):
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
def run_video_batch_preset(self, batch_id, preset_id=None):
    try:
        batch = VideoBatch.objects.get(id=batch_id)
    except VideoBatch.DoesNotExist:
        logger.error("Video batch %s does not exist", batch_id)
        return

    validation = validate_batch_preset(preset_id)
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

    plugin_manager = PluginManager()
    if batch.status == VideoBatch.STATUS_CANCELLED:
        cancel_batch_work(batch)
        return

    batch.status = VideoBatch.STATUS_RUNNING
    batch.preset = preset_id
    batch.save(update_fields=["status", "preset", "update_date"])

    for item in batch.items.filter(
        ingest_status=VideoBatchItem.STATUS_READY,
        video__isnull=False,
    ).order_by("original_path", "original_filename"):
        if is_batch_cancelled(batch):
            cancel_batch_work(batch)
            return

        outputs = {}
        for step_index, step in enumerate(preset["steps"]):
            if is_batch_cancelled(batch):
                cancel_batch_work(batch)
                return

            tracker, _ = VideoBatchPluginRun.objects.get_or_create(
                batch=batch,
                item=item,
                preset=preset_id,
                step_index=step_index,
                plugin=step["plugin"],
                defaults={"status": VideoBatchPluginRun.STATUS_PENDING},
            )
            if tracker.status == VideoBatchPluginRun.STATUS_DONE:
                outputs[step["plugin"]] = get_completed_step_outputs(tracker)
                continue

            parameters = build_step_parameters(step, outputs)
            if parameters is None:
                tracker.status = VideoBatchPluginRun.STATUS_ERROR
                tracker.error = "dependency_not_ready"
                tracker.save(update_fields=["status", "error", "update_date"])
                break

            tracker.status = VideoBatchPluginRun.STATUS_RUNNING
            tracker.error = ""
            tracker.save(update_fields=["status", "error", "update_date"])

            result = plugin_manager(
                step["plugin"],
                user=batch.owner,
                video=item.video,
                run_async=False,
                parameters=parameters,
            )
            plugin_run_id = result.get("plugin_run")
            if plugin_run_id:
                try:
                    tracker.plugin_run = PluginRun.objects.get(id=plugin_run_id)
                except PluginRun.DoesNotExist:
                    tracker.plugin_run = None

            if is_batch_cancelled(batch):
                tracker.status = VideoBatchPluginRun.STATUS_SKIPPED
                tracker.error = "cancelled"
                tracker.save(
                    update_fields=["plugin_run", "status", "error", "update_date"]
                )
                cancel_batch_work(batch)
                return

            if not result.get("status"):
                tracker.status = VideoBatchPluginRun.STATUS_ERROR
                tracker.error = "plugin_run_failed"
                tracker.save(
                    update_fields=["plugin_run", "status", "error", "update_date"]
                )
                break

            tracker.status = VideoBatchPluginRun.STATUS_DONE
            tracker.error = ""
            tracker.save(
                update_fields=["plugin_run", "status", "error", "update_date"]
            )
            outputs[step["plugin"]] = result.get("result", {})

    if is_batch_cancelled(batch):
        cancel_batch_work(batch)
        return

    if batch.plugin_runs.filter(status=VideoBatchPluginRun.STATUS_ERROR).exists():
        batch.status = VideoBatch.STATUS_PARTIAL_ERROR
    else:
        batch.refresh_counters(save=False)
        if batch.status != VideoBatch.STATUS_PARTIAL_ERROR:
            batch.status = VideoBatch.STATUS_READY
    batch.save()
