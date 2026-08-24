import json
import logging
import shutil
from pathlib import Path

from django.http import JsonResponse
from django.views import View

from backend.models import VideoBatch, VideoBatchItem, VideoBatchPluginRun
from backend.tasks.batch import ingest_video_batch, run_video_batch_preset
from backend.utils.batch_upload import (
    get_batch_dir,
    get_max_active_batch_ingests_per_user,
    get_max_batch_files,
    get_max_batch_total_size,
    save_batch_source_file,
)
from backend.utils.video_ingest import is_allowed_video_extension
from backend.utils.plugin_presets import (
    DEFAULT_BATCH_PRESET,
    list_batch_presets,
    validate_batch_preset,
)


logger = logging.getLogger(__name__)


def parse_batch_paths(request):
    try:
        return json.loads(request.POST.get("paths", "{}"))
    except json.JSONDecodeError:
        return {}


def path_for_file(paths, index, uploaded_file):
    if isinstance(paths, list) and index < len(paths):
        return paths[index]
    if isinstance(paths, dict):
        return paths.get(uploaded_file.name, uploaded_file.name)
    return uploaded_file.name


def enqueue_batch_ingest(batch):
    ingest_video_batch.apply_async((batch.id,))


class VideoBatchUpload(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            uploaded_files = request.FILES.getlist("files")
            single_file = request.FILES.get("file")
            zip_file = request.FILES.get("zip")
            if not uploaded_files and single_file is not None:
                if single_file.name.lower().endswith(".zip"):
                    zip_file = single_file
                else:
                    uploaded_files = [single_file]

            if zip_file is None and not uploaded_files:
                return JsonResponse(
                    {"status": "error", "type": "missing_values"}, status=500
                )

            if zip_file is not None and uploaded_files:
                return JsonResponse(
                    {"status": "error", "type": "wrong_request_body"}, status=500
                )

            active_count = VideoBatch.objects.filter(
                owner=request.user,
                status__in=[
                    VideoBatch.STATUS_UPLOADING,
                    VideoBatch.STATUS_INGESTING,
                    VideoBatch.STATUS_RUNNING,
                ],
            ).count()
            if active_count >= get_max_active_batch_ingests_per_user():
                return JsonResponse(
                    {"status": "error", "type": "too_many_active_batches"},
                    status=500,
                )

            name = request.POST.get("name")
            if not name:
                name = Path(zip_file.name if zip_file is not None else uploaded_files[0].name).stem

            batch = VideoBatch.objects.create(
                owner=request.user,
                name=name,
                preset=request.POST.get("preset") or None,
                auto_run_preset=request.POST.get("auto_run_preset", "false").lower()
                == "true",
                source_type=(
                    VideoBatch.SOURCE_ZIP if zip_file is not None else VideoBatch.SOURCE_FILES
                ),
            )

            if zip_file is not None:
                if not zip_file.name.lower().endswith(".zip"):
                    batch.status = VideoBatch.STATUS_ERROR
                    batch.save(update_fields=["status", "update_date"])
                    return JsonResponse(
                        {"status": "error", "type": "wrong_file_extension"},
                        status=500,
                    )

                source = save_batch_source_file(batch.id, zip_file, prefix="batch")
                if source["file_size"] > get_max_batch_total_size():
                    batch.status = VideoBatch.STATUS_ERROR
                    batch.save(update_fields=["status", "update_date"])
                    return JsonResponse(
                        {"status": "error", "type": "batch_too_large"}, status=500
                    )
                batch.source_path = str(source["path"])
                batch.save(update_fields=["source_path", "update_date"])
            else:
                if len(uploaded_files) > get_max_batch_files():
                    batch.status = VideoBatch.STATUS_ERROR
                    batch.save(update_fields=["status", "update_date"])
                    return JsonResponse(
                        {"status": "error", "type": "too_many_files"}, status=500
                    )

                total_size = sum(file.size for file in uploaded_files)
                if total_size > get_max_batch_total_size():
                    batch.status = VideoBatch.STATUS_ERROR
                    batch.save(update_fields=["status", "update_date"])
                    return JsonResponse(
                        {"status": "error", "type": "batch_too_large"}, status=500
                    )

                paths = parse_batch_paths(request)
                for index, uploaded_file in enumerate(uploaded_files):
                    original_path = path_for_file(paths, index, uploaded_file)
                    if not is_allowed_video_extension(uploaded_file.name):
                        VideoBatchItem.objects.create(
                            batch=batch,
                            original_filename=uploaded_file.name,
                            original_path=original_path,
                            file_size=uploaded_file.size,
                            ingest_status=VideoBatchItem.STATUS_ERROR,
                            ingest_error="wrong_file_extension",
                        )
                        continue

                    source = save_batch_source_file(
                        batch.id, uploaded_file, prefix=f"{index:06d}"
                    )
                    VideoBatchItem.objects.create(
                        batch=batch,
                        original_filename=uploaded_file.name,
                        original_path=original_path,
                        source_path=str(source["path"]),
                        file_size=source["file_size"],
                        checksum=source["checksum"],
                    )

                batch.refresh_counters()

            enqueue_batch_ingest(batch)
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to upload video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchList(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            entries = [
                batch.to_dict()
                for batch in VideoBatch.objects.filter(owner=request.user).order_by("-date")
            ]
            return JsonResponse({"status": "ok", "entries": entries})
        except Exception:
            logger.exception("Failed to list video batches")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchGet(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                batch = VideoBatch.objects.get(
                    id=request.GET.get("id"), owner=request.user
                )
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            return JsonResponse(
                {"status": "ok", "entry": batch.to_dict(include_items=True, include_videos=True)}
            )
        except Exception:
            logger.exception("Failed to get video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchRetryFailed(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"status": "error", "type": "wrong_request_body"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            VideoBatchItem.objects.filter(
                batch=batch,
                ingest_status=VideoBatchItem.STATUS_ERROR,
            ).exclude(source_path__isnull=True).exclude(source_path="").update(
                ingest_status=VideoBatchItem.STATUS_PENDING,
                ingest_error="",
            )
            enqueue_batch_ingest(batch)
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to retry video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchDelete(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"status": "error", "type": "wrong_request_body"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            batch_dir = get_batch_dir(batch.id)
            batch.delete()
            if batch_dir.exists():
                shutil.rmtree(batch_dir)
            return JsonResponse({"status": "ok"})
        except Exception:
            logger.exception("Failed to delete video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchCancel(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"status": "error", "type": "wrong_request_body"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

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
            batch.status = VideoBatch.STATUS_ERROR
            batch.save(update_fields=["status", "update_date"])
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to cancel video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchPresetList(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            return JsonResponse({"status": "ok", "entries": list_batch_presets()})
        except Exception:
            logger.exception("Failed to list video batch presets")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchRunPreset(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"status": "error", "type": "wrong_request_body"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            preset = data.get("preset") or batch.preset or DEFAULT_BATCH_PRESET
            validation = validate_batch_preset(preset)
            if validation["status"] != "ok":
                return JsonResponse(validation, status=500)

            batch.preset = preset
            batch.save(update_fields=["preset", "update_date"])
            run_video_batch_preset.apply_async((batch.id, preset))
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to run video batch preset")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchRetryFailedPluginSteps(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"status": "error", "type": "wrong_request_body"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            VideoBatchPluginRun.objects.filter(
                batch=batch,
                status=VideoBatchPluginRun.STATUS_ERROR,
            ).update(status=VideoBatchPluginRun.STATUS_PENDING, error="")

            preset = data.get("preset") or batch.preset or DEFAULT_BATCH_PRESET
            run_video_batch_preset.apply_async((batch.id, preset))
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to retry failed video batch plugin steps")
            return JsonResponse({"status": "error"}, status=500)
