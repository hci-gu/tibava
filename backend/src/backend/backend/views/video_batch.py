import json
import io
import logging
import shutil
import uuid
import zipfile
from pathlib import Path

from django.http import HttpResponse, JsonResponse
from django.utils.text import slugify
from django.views import View

from backend.models import VideoBatch, VideoBatchItem, VideoBatchPluginRun
from backend.tasks.batch import cancel_batch_work, ingest_video_batch, run_video_batch_preset
from backend.utils.batch_upload import (
    get_batch_dir,
    get_max_active_batch_ingests_per_user,
    get_max_batch_files,
    get_max_batch_total_size,
    save_batch_source_file,
    sha256_path,
    normalize_zip_member_name,
)
from backend.utils.batch_plugin_catalog import list_batch_plugin_catalog, timeline_by_name
from backend.utils.video_ingest import is_allowed_video_extension
from backend.utils.plugin_presets import (
    DEFAULT_BATCH_PRESET,
    list_batch_presets,
    normalize_custom_batch_preset,
    validate_batch_preset,
)
from backend.views.video_export import ElanExportError, VideoExport


logger = logging.getLogger(__name__)


def elan_archive_path(item, used_paths):
    normalized_path = normalize_zip_member_name(
        item.original_path or item.original_filename
    )
    if normalized_path is None:
        normalized_path = f"{item.video_id.hex}.eaf"
    else:
        normalized_path = str(Path(normalized_path).with_suffix(".eaf")).replace(
            "\\", "/"
        )

    candidate = normalized_path
    index = 2
    while candidate.casefold() in used_paths:
        path = Path(normalized_path)
        candidate = str(path.with_name(f"{path.stem} ({index}){path.suffix}")).replace(
            "\\", "/"
        )
        index += 1
    used_paths.add(candidate.casefold())
    return candidate


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


def user_has_active_batch_capacity(batch):
    active_count = (
        VideoBatch.objects.filter(
            owner=batch.owner,
            status__in=[
                VideoBatch.STATUS_UPLOADING,
                VideoBatch.STATUS_INGESTING,
                VideoBatch.STATUS_RUNNING,
            ],
        )
        .exclude(id=batch.id)
        .count()
    )
    return active_count < get_max_active_batch_ingests_per_user()


def ready_item_ids_for_scope(batch, scope):
    if not isinstance(scope, dict):
        scope = {"type": "all"}

    scope_type = scope.get("type") or "all"
    items = VideoBatchItem.objects.filter(
        batch=batch,
        ingest_status=VideoBatchItem.STATUS_READY,
        video__isnull=False,
    )

    if scope_type == "item_ids":
        requested_ids = scope.get("item_ids") or []
        if not isinstance(requested_ids, list) or not requested_ids:
            return {"status": "error", "type": "missing_item_ids"}
        requested_ids = [str(item_id) for item_id in requested_ids]
        items = items.filter(id__in=requested_ids)
        item_ids = [item.id for item in items]
        if len(item_ids) != len(set(requested_ids)):
            return {"status": "error", "type": "invalid_item_ids"}
        return {"status": "ok", "item_ids": item_ids}

    if scope_type == "folder":
        folder_path = (scope.get("folder_path") or "").strip("/")
        include_subfolders = scope.get("include_subfolders", True)
        if folder_path:
            prefix = f"{folder_path}/"
            if include_subfolders:
                items = items.filter(original_path__startswith=prefix)
            else:
                depth = folder_path.count("/") + 1
                items = [
                    item
                    for item in items
                    if item.original_path.startswith(prefix)
                    and item.original_path.count("/") == depth
                ]
                item_ids = [item.id for item in items]
                return {"status": "ok", "item_ids": item_ids}
        else:
            items = [item for item in items if "/" not in item.original_path]
            item_ids = [item.id for item in items]
            return {"status": "ok", "item_ids": item_ids}

    elif scope_type != "all":
        return {"status": "error", "type": "invalid_scope"}

    item_ids = list(items.values_list("id", flat=True))
    if not item_ids:
        return {"status": "error", "type": "no_ready_items"}
    return {"status": "ok", "item_ids": item_ids}


def preflight_batch_plugin_set(batch, data):
    preset_result = normalize_custom_batch_preset(
        data.get("steps"),
        name=data.get("name") or "Custom batch analysis",
    )
    if preset_result["status"] != "ok":
        return preset_result

    scope_result = ready_item_ids_for_scope(batch, data.get("scope"))
    if scope_result["status"] != "ok":
        return scope_result

    preset = preset_result["preset"]
    items = list(
        VideoBatchItem.objects.filter(
            batch=batch,
            id__in=scope_result["item_ids"],
            ingest_status=VideoBatchItem.STATUS_READY,
            video__isnull=False,
        ).select_related("video")
    )
    skipped = {}

    for item in items:
        for step in preset["steps"]:
            for resolution in step.get("parameter_resolution", {}).values():
                strategy = resolution.get("strategy")
                if strategy == "timeline_by_name":
                    if timeline_by_name(item.video, resolution.get("name", "")) is None:
                        skipped[item.id.hex] = "missing_required_timeline"
                        break
                elif strategy == "scalar_timeline_by_name":
                    if timeline_by_name(item.video, resolution.get("name", ""), scalar=True) is None:
                        skipped[item.id.hex] = "missing_required_timeline"
                        break
                elif strategy == "scalar_timelines_by_name":
                    names = resolution.get("names", [])
                    if not names:
                        skipped[item.id.hex] = "missing_required_timeline"
                        break
                    if any(
                        timeline_by_name(item.video, name, scalar=True) is None
                        for name in names
                    ):
                        skipped[item.id.hex] = "missing_required_timeline"
                        break
                elif strategy == "shared_file" and "path" not in resolution:
                    return {"status": "error", "type": "shared_input_missing"}
            if item.id.hex in skipped:
                break

    runnable_item_ids = [
        item.id for item in items if item.id.hex not in skipped
    ]
    if not runnable_item_ids:
        return {
            "status": "error",
            "type": "no_runnable_items",
            "preset": preset,
            "preset_id": preset_result["preset_id"],
            "skipped_items": [
                {"item_id": item_id, "reason": reason}
                for item_id, reason in skipped.items()
            ],
        }

    return {
        "status": "ok",
        "preset": preset,
        "preset_id": preset_result["preset_id"],
        "item_ids": runnable_item_ids,
        "runnable_count": len(runnable_item_ids),
        "skipped_count": len(skipped),
        "step_count": len(preset["steps"]),
        "total_jobs": len(runnable_item_ids) * len(preset["steps"]),
        "skipped_items": [
            {"item_id": item_id, "reason": reason}
            for item_id, reason in skipped.items()
        ],
    }


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

            preset = request.POST.get("preset") or None
            auto_run_preset = (
                request.POST.get("auto_run_preset", "false").lower() == "true"
            )
            if auto_run_preset and not preset:
                preset = DEFAULT_BATCH_PRESET
            if preset:
                validation = validate_batch_preset(preset)
                if validation["status"] != "ok":
                    return JsonResponse(validation, status=500)

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
                preset=preset,
                auto_run_preset=auto_run_preset and preset is not None,
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


class VideoBatchSharedInputUpload(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error", "type": "not_authenticated"}, status=403)

            batch_id = request.POST.get("id")
            uploaded_file = request.FILES.get("file")
            if not batch_id or uploaded_file is None:
                return JsonResponse({"status": "error", "type": "missing_values"}, status=500)

            try:
                batch = VideoBatch.objects.get(id=batch_id, owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            suffix = Path(uploaded_file.name).suffix.lower()
            shared_dir = get_batch_dir(batch.id) / "shared_inputs"
            shared_dir.mkdir(parents=True, exist_ok=True)
            output_path = shared_dir / f"{uuid.uuid4().hex}{suffix}"

            with output_path.open("wb") as output:
                for chunk in uploaded_file.chunks():
                    output.write(chunk)

            return JsonResponse(
                {
                    "status": "ok",
                    "entry": {
                        "origin": uploaded_file.name,
                        "path": str(output_path),
                        "file_size": output_path.stat().st_size,
                        "checksum": sha256_path(output_path),
                    },
                }
            )
        except Exception:
            logger.exception("Failed to upload shared batch plugin input")
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


class VideoBatchExportElan(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"status": "error", "type": "not_authenticated"}, status=500
            )

        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse(
                {"status": "error", "type": "wrong_request_body"}, status=500
            )

        try:
            batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
        except (ValueError, VideoBatch.DoesNotExist):
            return JsonResponse(
                {"status": "error", "type": "not_exist"}, status=500
            )

        items = batch.items.filter(
            ingest_status=VideoBatchItem.STATUS_READY,
            video__isnull=False,
            video__owner=request.user,
        ).select_related("video")
        if not items.exists():
            return JsonResponse(
                {"status": "error", "type": "no_ready_items"}, status=500
            )

        buffer = io.BytesIO()
        report = {"exported": [], "failed": []}
        used_paths = set()
        exporter = VideoExport()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in items.order_by("original_path", "original_filename"):
                archive_path = elan_archive_path(item, used_paths)
                try:
                    linked_file_path = Path(
                        item.original_path or item.original_filename
                    ).name
                    elan = exporter.export_elan(
                        {"aggregation": 0},
                        item.video,
                        linked_file_path=linked_file_path,
                    )
                    archive.writestr(archive_path, elan)
                    report["exported"].append(
                        {"item_id": item.id.hex, "path": archive_path}
                    )
                except ElanExportError as exc:
                    report["failed"].append(
                        {
                            "item_id": item.id.hex,
                            "original_path": item.original_path,
                            "reason": exc.code,
                        }
                    )
                except Exception:
                    logger.exception(
                        "Failed to export ELAN for batch item %s", item.id.hex
                    )
                    report["failed"].append(
                        {
                            "item_id": item.id.hex,
                            "original_path": item.original_path,
                            "reason": "elan_export_failed",
                        }
                    )

            if report["failed"]:
                archive.writestr(
                    "export-report.json",
                    json.dumps(report, indent=2),
                )

        filename = f"{slugify(batch.name) or batch.id.hex}-elan.zip"
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Exported-Count"] = str(len(report["exported"]))
        response["X-Failed-Count"] = str(len(report["failed"]))
        return response


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

            cancel_batch_work(batch)
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


class VideoBatchPluginCatalog(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            return JsonResponse(
                {"status": "ok", "entries": list_batch_plugin_catalog()}
            )
        except Exception:
            logger.exception("Failed to list batch plugin catalog")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchValidatePluginSet(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            try:
                data = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse(
                    {"status": "error", "type": "wrong_request_body"}, status=500
                )

            try:
                batch = VideoBatch.objects.get(id=data.get("id"), owner=request.user)
            except VideoBatch.DoesNotExist:
                return JsonResponse({"status": "error", "type": "not_exist"}, status=500)

            result = preflight_batch_plugin_set(batch, data)
            if result["status"] != "ok":
                return JsonResponse(result, status=500)

            return JsonResponse(
                {
                    "status": "ok",
                    "preset": result["preset_id"],
                    "runnable_count": result["runnable_count"],
                    "skipped_count": result["skipped_count"],
                    "step_count": result["step_count"],
                    "total_jobs": result["total_jobs"],
                    "skipped_items": result["skipped_items"],
                }
            )
        except Exception:
            logger.exception("Failed to validate video batch plugin set")
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

            if not user_has_active_batch_capacity(batch):
                return JsonResponse(
                    {"status": "error", "type": "too_many_active_batches"},
                    status=500,
                )

            batch.preset = preset
            batch.custom_preset_definition = None
            batch.custom_preset_item_ids = None
            batch.save(
                update_fields=[
                    "preset",
                    "custom_preset_definition",
                    "custom_preset_item_ids",
                    "update_date",
                ]
            )
            scope_result = ready_item_ids_for_scope(batch, data.get("scope"))
            if scope_result["status"] != "ok":
                return JsonResponse(scope_result, status=500)

            run_video_batch_preset.apply_async(
                (batch.id, preset, scope_result["item_ids"], None)
            )
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to run video batch preset")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchRunPluginSet(View):
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

            if not user_has_active_batch_capacity(batch):
                return JsonResponse(
                    {"status": "error", "type": "too_many_active_batches"},
                    status=500,
                )

            preflight = preflight_batch_plugin_set(batch, data)
            if preflight["status"] != "ok":
                return JsonResponse(preflight, status=500)

            batch.preset = preflight["preset_id"]
            batch.custom_preset_definition = preflight["preset"]
            batch.custom_preset_item_ids = [
                item_id.hex for item_id in preflight["item_ids"]
            ]
            batch.save(
                update_fields=[
                    "preset",
                    "custom_preset_definition",
                    "custom_preset_item_ids",
                    "update_date",
                ]
            )
            run_video_batch_preset.apply_async(
                (
                    batch.id,
                    preflight["preset_id"],
                    preflight["item_ids"],
                    preflight["preset"],
                )
            )
            return JsonResponse(
                {
                    "status": "ok",
                    "batch_id": batch.id.hex,
                    "preset": preflight["preset_id"],
                    "item_count": len(preflight["item_ids"]),
                    "skipped_count": preflight["skipped_count"],
                    "total_jobs": preflight["total_jobs"],
                }
            )
        except Exception:
            logger.exception("Failed to run video batch plugin set")
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
            if not user_has_active_batch_capacity(batch):
                return JsonResponse(
                    {"status": "error", "type": "too_many_active_batches"},
                    status=500,
                )
            run_video_batch_preset.apply_async(
                (
                    batch.id,
                    preset,
                    getattr(batch, "custom_preset_item_ids", None),
                    getattr(batch, "custom_preset_definition", None),
                )
            )
            return JsonResponse({"status": "ok", "batch_id": batch.id.hex})
        except Exception:
            logger.exception("Failed to retry failed video batch plugin steps")
            return JsonResponse({"status": "error"}, status=500)
