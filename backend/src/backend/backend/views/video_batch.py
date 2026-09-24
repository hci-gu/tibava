import json
import io
import logging
import shutil
import uuid
from pathlib import Path

from django.core.cache import cache
from django.db.models import Case, CharField, Count, F, Q, When
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils.text import slugify
from django.views import View

from backend.models import (
    SavedBatchPreset,
    VideoBatch,
    VideoBatchItem,
    VideoBatchPluginRun,
)
from backend.tasks.batch import (
    cancel_batch_work,
    export_batch_elan,
    ingest_video_batch,
    run_video_batch_preset,
)
from backend.utils.batch_upload import (
    get_batch_dir,
    get_max_active_batch_ingests_per_user,
    get_max_batch_files,
    get_max_batch_total_size,
    save_batch_source_file,
    sha256_path,
    repair_filename_unicode,
)
from backend.utils.batch_plugin_catalog import list_batch_plugin_catalog, timeline_by_name
from backend.utils.batch_naming import numbered_batch_video_path
from backend.utils.elan_export import (
    ELAN_EXPORT_CACHE_TIMEOUT,
    build_elan_archive,
    elan_export_cache_key,
)
from backend.utils.video_ingest import is_allowed_video_extension
from backend.utils.plugin_presets import (
    DEFAULT_BATCH_PRESET,
    list_batch_presets,
    normalize_custom_batch_preset,
    validate_batch_preset,
    validate_batch_preset_definition,
)


logger = logging.getLogger(__name__)

ITEM_STATUS_CODES = {label: code for code, label in VideoBatchItem.STATUS.items()}
PLUGIN_STATUS_CODES = {label: code for code, label in VideoBatchPluginRun.STATUS.items()}


def with_display_path(items):
    return items.annotate(
        effective_path=Case(
            When(display_path="", then=F("original_path")),
            default=F("display_path"),
            output_field=CharField(),
        )
    )


def filtered_batch_items(batch, filters):
    items = with_display_path(batch.items.all())
    status = filters.get("status") or "All"
    if status != "All":
        status_filter = Q()
        if status in ITEM_STATUS_CODES:
            status_filter |= Q(ingest_status=ITEM_STATUS_CODES[status])
        if status in PLUGIN_STATUS_CODES:
            status_filter |= Q(plugin_runs__status=PLUGIN_STATUS_CODES[status])
        items = items.filter(status_filter).distinct() if status_filter else items.none()

    folder = filters.get("folder")
    if folder is not None:
        folder = str(folder).strip("/")
        items = (
            items.filter(effective_path__startswith=f"{folder}/")
            if folder
            else items.exclude(effective_path__contains="/")
        )

    search = (filters.get("search") or "").strip()
    if search:
        items = items.filter(
            Q(effective_path__icontains=search)
            | Q(original_path__icontains=search)
            | Q(original_filename__icontains=search)
        )
    return items


def batch_detail_summary(batch):
    item_counts = {status: 0 for status in ITEM_STATUS_CODES}
    folders = {}
    for displayed_path, status, video_id in with_display_path(
        batch.items.all()
    ).values_list("effective_path", "ingest_status", "video_id"):
        item_counts[VideoBatchItem.STATUS[status]] += 1
        folder_path = displayed_path.rpartition("/")[0]
        folder = folders.setdefault(
            folder_path, {"path": folder_path, "count": 0, "ready_count": 0}
        )
        folder["count"] += 1
        if status == VideoBatchItem.STATUS_READY and video_id:
            folder["ready_count"] += 1

    plugin_counts = {status: 0 for status in PLUGIN_STATUS_CODES}
    for row in batch.plugin_runs.values("status").annotate(count=Count("id")):
        plugin_counts[VideoBatchPluginRun.STATUS[row["status"]]] = row["count"]

    plugin_columns = []
    for plugin in batch.plugin_runs.order_by("step_index", "plugin").values_list(
        "plugin", flat=True
    ).distinct():
        if plugin not in plugin_columns:
            plugin_columns.append(plugin)

    return {
        "item_status_counts": item_counts,
        "plugin_status_counts": plugin_counts,
        "folders": list(folders.values()),
        "plugin_columns": plugin_columns,
    }


def elan_export_filename(batch, apply_filtering):
    batch_name = (slugify(batch.name) or batch.id.hex)[:64].rstrip("-")
    suffix = "elan" if apply_filtering else "raw-elan"
    return f"{batch_name or batch.id.hex[:12]}-{suffix}.zip"


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


def reset_batch_plugin_runs(batch, preset_id, item_ids):
    """Remove prior scheduler state so a manual run executes again."""
    VideoBatchPluginRun.objects.filter(
        batch=batch,
        preset=preset_id,
        item_id__in=item_ids,
    ).delete()


def resolve_batch_preset_for_run(batch, requested_preset=None):
    preset_id = requested_preset or batch.preset or DEFAULT_BATCH_PRESET
    preset_definition = None
    saved_id = saved_preset_uuid(preset_id)
    if saved_id is not None:
        preset_definition, validation = resolve_saved_or_builtin_preset(
            batch.owner,
            preset_id,
        )
        if (
            validation["status"] != "ok"
            and preset_id == batch.preset
            and batch.custom_preset_definition is not None
        ):
            preset_definition = batch.custom_preset_definition
            validation = validate_batch_preset_definition(preset_definition)
    elif preset_id == batch.preset and batch.custom_preset_definition is not None:
        preset_definition = batch.custom_preset_definition
        validation = validate_batch_preset_definition(preset_definition)
    else:
        preset_definition, validation = resolve_saved_or_builtin_preset(
            batch.owner,
            preset_id,
        )
    return preset_id, preset_definition, validation


def saved_preset_uuid(preset_id):
    if not isinstance(preset_id, str) or not preset_id.startswith("saved:"):
        return None
    try:
        return uuid.UUID(preset_id.removeprefix("saved:"))
    except (ValueError, AttributeError):
        return None


def resolve_saved_or_builtin_preset(owner, preset_id):
    saved_id = saved_preset_uuid(preset_id)
    if saved_id is None:
        return None, validate_batch_preset(preset_id)
    try:
        saved = SavedBatchPreset.objects.get(id=saved_id, owner=owner)
    except SavedBatchPreset.DoesNotExist:
        return None, {"status": "error", "type": "not_exist"}
    definition = saved.definition
    return definition, validate_batch_preset_definition(definition)


def validate_reusable_preset_definition(definition):
    for step in definition.get("steps", []):
        for resolution in step.get("parameter_resolution", {}).values():
            if resolution.get("strategy") == "shared_file":
                return {
                    "status": "error",
                    "type": "shared_file_preset_not_supported",
                }
    return {"status": "ok"}


def ready_item_ids_for_scope(batch, scope):
    if not isinstance(scope, dict):
        scope = {"type": "all"}

    scope_type = scope.get("type") or "all"
    items = with_display_path(
        VideoBatchItem.objects.filter(
            batch=batch,
            ingest_status=VideoBatchItem.STATUS_READY,
            video__isnull=False,
        )
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

    if scope_type == "filtered":
        items = filtered_batch_items(batch, scope).filter(
            ingest_status=VideoBatchItem.STATUS_READY,
            video__isnull=False,
        )

    elif scope_type == "folder":
        folder_path = (scope.get("folder_path") or "").strip("/")
        include_subfolders = scope.get("include_subfolders", True)
        if folder_path:
            prefix = f"{folder_path}/"
            if include_subfolders:
                items = items.filter(effective_path__startswith=prefix)
            else:
                depth = folder_path.count("/") + 1
                items = [
                    item
                    for item in items
                    if item.effective_path.startswith(prefix)
                    and item.effective_path.count("/") == depth
                ]
                item_ids = [item.id for item in items]
                return {"status": "ok", "item_ids": item_ids}
        else:
            items = [item for item in items if "/" not in item.effective_path]
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
            preset_definition = None
            if preset:
                preset_definition, validation = resolve_saved_or_builtin_preset(
                    request.user,
                    preset,
                )
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
                custom_preset_definition=preset_definition,
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
                max_batch_files = get_max_batch_files()
                if (
                    max_batch_files is not None
                    and len(uploaded_files) > max_batch_files
                ):
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
                    original_path = repair_filename_unicode(
                        path_for_file(paths, index, uploaded_file)
                    )
                    if not is_allowed_video_extension(uploaded_file.name):
                        continue

                    source = save_batch_source_file(
                        batch.id, uploaded_file, prefix=f"{index:06d}"
                    )
                    VideoBatchItem.objects.create(
                        batch=batch,
                        original_filename=repair_filename_unicode(uploaded_file.name),
                        original_path=original_path,
                        display_path=numbered_batch_video_path(
                            original_path, slug_channel=True
                        ) or "",
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

            summary_only = request.GET.get("summary", "false").lower() == "true"
            entry = batch.to_dict(
                include_items=not summary_only,
                include_videos=not summary_only,
            )
            if summary_only:
                entry.update(batch_detail_summary(batch))
            return JsonResponse(
                {
                    "status": "ok",
                    "entry": entry,
                }
            )
        except Exception:
            logger.exception("Failed to get video batch")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchItems(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({"status": "error", "type": "not_authenticated"}, status=403)
        try:
            batch = VideoBatch.objects.get(id=request.GET.get("id"), owner=request.user)
        except (ValueError, VideoBatch.DoesNotExist):
            return JsonResponse({"status": "error", "type": "not_exist"}, status=404)

        try:
            page = max(1, int(request.GET.get("page", 1)))
            page_size = int(request.GET.get("page_size", 50))
            if page_size < 1 or page_size > 100:
                raise ValueError
        except ValueError:
            return JsonResponse({"status": "error", "type": "invalid_page"}, status=400)

        items = filtered_batch_items(batch, request.GET)
        total = items.count()
        ready_count = items.filter(
            ingest_status=VideoBatchItem.STATUS_READY,
            video__isnull=False,
        ).count()
        page = min(page, max(1, (total + page_size - 1) // page_size))
        sort_fields = {
            "original_path": "effective_path",
            "display_path": "effective_path",
            "original_filename": "original_filename",
            "display_filename": "original_filename",
            "ingest_status": "ingest_status",
            "date": "date",
        }
        sort_field = sort_fields.get(request.GET.get("sort_by"), "effective_path")
        if request.GET.get("sort_desc") == "true":
            sort_field = f"-{sort_field}"
        start = (page - 1) * page_size
        page_items = list(
            items.select_related("video")
            .order_by(sort_field, "id")[start : start + page_size]
        )
        item_ids = [item.id for item in page_items]
        plugin_runs = VideoBatchPluginRun.objects.filter(
            batch=batch, item_id__in=item_ids
        ).select_related("item", "item__video")
        return JsonResponse(
            {
                "status": "ok",
                "items": [item.to_dict(include_video=True) for item in page_items],
                "plugin_runs": [run.to_dict() for run in plugin_runs],
                "total": total,
                "ready_count": ready_count,
                "page": page,
                "page_size": page_size,
            }
        )


class VideoBatchItemIds(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({"status": "error", "type": "not_authenticated"}, status=403)
        try:
            batch = VideoBatch.objects.get(id=request.GET.get("id"), owner=request.user)
        except (ValueError, VideoBatch.DoesNotExist):
            return JsonResponse({"status": "error", "type": "not_exist"}, status=404)

        folder = request.GET.get("folder")
        exact_folder = request.GET.get("exact_folder") == "true"
        if exact_folder and folder is None:
            return JsonResponse({"status": "error", "type": "missing_folder"}, status=400)
        entries = []
        for item_id, original_path, displayed_path, status, video_id in filtered_batch_items(
            batch, request.GET
        ).values_list(
            "id", "original_path", "effective_path", "ingest_status", "video_id"
        ):
            if exact_folder and displayed_path.rpartition("/")[0] != folder.strip("/"):
                continue
            entries.append(
                {
                    "id": item_id.hex,
                    "original_path": original_path,
                    "display_path": displayed_path,
                    "ingest_status": VideoBatchItem.STATUS[status],
                    "video_id": video_id.hex if video_id else None,
                }
            )
        return JsonResponse({"status": "ok", "entries": entries})


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

        apply_filtering = data.get("apply_filtering", True)
        if not isinstance(apply_filtering, bool):
            return JsonResponse(
                {"status": "error", "type": "wrong_request_body"}, status=500
            )

        if data.get("async"):
            job_id = uuid.uuid4().hex
            archive_path = get_batch_dir(batch.id) / "elan-exports" / f"{job_id}.zip"
            filename = elan_export_filename(batch, apply_filtering)
            total = items.count()
            cache.set(
                elan_export_cache_key(job_id),
                {
                    "job_id": job_id,
                    "batch_id": batch.id.hex,
                    "owner_id": str(request.user.pk),
                    "archive_path": str(archive_path),
                    "filename": filename,
                    "apply_filtering": apply_filtering,
                    "status": "queued",
                    "phase": "queued",
                    "processed": 0,
                    "total": total,
                    "exported": 0,
                    "failed": 0,
                },
                ELAN_EXPORT_CACHE_TIMEOUT,
            )
            try:
                export_batch_elan.apply_async((job_id,))
            except Exception:
                logger.exception("Failed to queue batch ELAN export %s", job_id)
                cache.delete(elan_export_cache_key(job_id))
                return JsonResponse(
                    {"status": "error", "type": "export_queue_failed"}, status=500
                )
            return JsonResponse(
                {
                    "status": "ok",
                    "job_id": job_id,
                    "total": total,
                },
                status=202,
            )

        buffer = io.BytesIO()
        report = build_elan_archive(
            items.order_by("original_path", "original_filename"),
            buffer,
            apply_filtering=apply_filtering,
        )

        filename = elan_export_filename(batch, apply_filtering)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Exported-Count"] = str(len(report["exported"]))
        response["X-Failed-Count"] = str(len(report["failed"]))
        return response


class VideoBatchExportElanStatus(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"status": "error", "type": "not_authenticated"}, status=500
            )

        state = cache.get(elan_export_cache_key(request.GET.get("id", "")))
        if state is None or state.get("owner_id") != str(request.user.pk):
            return JsonResponse({"status": "error", "type": "not_exist"}, status=404)

        total = state.get("total", 0)
        processed = state.get("processed", 0)
        progress = 0 if not total else round(processed * 100 / total)
        if state.get("status") == "complete":
            progress = 100
        return JsonResponse(
            {
                "status": state.get("status"),
                "phase": state.get("phase"),
                "processed": processed,
                "total": total,
                "exported": state.get("exported", 0),
                "failed": state.get("failed", 0),
                "progress": progress,
                "error": state.get("error"),
            }
        )


class VideoBatchExportElanDownload(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"status": "error", "type": "not_authenticated"}, status=500
            )

        state = cache.get(elan_export_cache_key(request.GET.get("id", "")))
        if state is None or state.get("owner_id") != str(request.user.pk):
            return JsonResponse({"status": "error", "type": "not_exist"}, status=404)
        if state.get("status") != "complete":
            return JsonResponse({"status": "error", "type": "not_ready"}, status=409)

        archive_path = Path(state["archive_path"])
        if not archive_path.is_file():
            return JsonResponse({"status": "error", "type": "not_exist"}, status=404)

        response = FileResponse(
            archive_path.open("rb"),
            as_attachment=True,
            filename=state["filename"],
            content_type="application/zip",
        )
        response["X-Exported-Count"] = str(state.get("exported", 0))
        response["X-Failed-Count"] = str(state.get("failed", 0))
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

            if batch.status not in {
                VideoBatch.STATUS_UPLOADING,
                VideoBatch.STATUS_INGESTING,
                VideoBatch.STATUS_RUNNING,
            }:
                return JsonResponse(
                    {"status": "error", "type": "batch_not_cancellable"},
                    status=409,
                )

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

            built_in = [
                {**preset, "source": "built_in", "editable": False}
                for preset in list_batch_presets()
            ]
            saved = [
                preset.to_dict()
                for preset in SavedBatchPreset.objects.filter(owner=request.user)
            ]
            return JsonResponse({"status": "ok", "entries": built_in + saved})
        except Exception:
            logger.exception("Failed to list video batch presets")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchPresetSave(View):
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

            name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            if not name:
                return JsonResponse(
                    {"status": "error", "type": "missing_name"}, status=500
                )
            if len(name) > 256 or len(description) > 1024:
                return JsonResponse(
                    {"status": "error", "type": "value_too_long"}, status=500
                )

            normalized = normalize_custom_batch_preset(data.get("steps"), name=name)
            if normalized["status"] != "ok":
                return JsonResponse(normalized, status=500)
            definition = normalized["preset"]
            definition["description"] = description
            reusable = validate_reusable_preset_definition(definition)
            if reusable["status"] != "ok":
                return JsonResponse(reusable, status=500)

            preset_id = data.get("id")
            saved_id = saved_preset_uuid(preset_id) if preset_id else None
            if preset_id and saved_id is None:
                return JsonResponse(
                    {"status": "error", "type": "not_exist"}, status=500
                )
            if SavedBatchPreset.objects.filter(
                owner=request.user,
                name=name,
            ).exclude(id=saved_id).exists():
                return JsonResponse(
                    {"status": "error", "type": "preset_name_exists"},
                    status=409,
                )

            if saved_id is None:
                saved = SavedBatchPreset.objects.create(
                    owner=request.user,
                    name=name,
                    description=description,
                    definition=definition,
                )
            else:
                try:
                    saved = SavedBatchPreset.objects.get(
                        id=saved_id,
                        owner=request.user,
                    )
                except SavedBatchPreset.DoesNotExist:
                    return JsonResponse(
                        {"status": "error", "type": "not_exist"}, status=500
                    )
                saved.name = name
                saved.description = description
                saved.definition = definition
                saved.save(
                    update_fields=[
                        "name",
                        "description",
                        "definition",
                        "update_date",
                    ]
                )

            return JsonResponse({"status": "ok", "entry": saved.to_dict()})
        except Exception:
            logger.exception("Failed to save batch preset")
            return JsonResponse({"status": "error"}, status=500)


class VideoBatchPresetDelete(View):
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

            saved_id = saved_preset_uuid(data.get("id"))
            if saved_id is None:
                return JsonResponse(
                    {"status": "error", "type": "not_exist"}, status=500
                )
            deleted, _ = SavedBatchPreset.objects.filter(
                id=saved_id,
                owner=request.user,
            ).delete()
            if deleted == 0:
                return JsonResponse(
                    {"status": "error", "type": "not_exist"}, status=500
                )
            return JsonResponse({"status": "ok"})
        except Exception:
            logger.exception("Failed to delete batch preset")
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

            preset, preset_definition, validation = resolve_batch_preset_for_run(
                batch,
                data.get("preset"),
            )
            if validation["status"] != "ok":
                return JsonResponse(validation, status=500)

            if not user_has_active_batch_capacity(batch):
                return JsonResponse(
                    {"status": "error", "type": "too_many_active_batches"},
                    status=500,
                )

            scope_result = ready_item_ids_for_scope(batch, data.get("scope"))
            if scope_result["status"] != "ok":
                return JsonResponse(scope_result, status=500)

            if batch.plugin_runs.filter(
                status=VideoBatchPluginRun.STATUS_RUNNING
            ).exists():
                return JsonResponse(
                    {"status": "error", "type": "batch_already_running"},
                    status=409,
                )

            batch.preset = preset
            batch.custom_preset_definition = preset_definition
            batch.custom_preset_item_ids = (
                [item_id.hex for item_id in scope_result["item_ids"]]
                if preset_definition is not None
                else None
            )
            batch.save(
                update_fields=[
                    "preset",
                    "custom_preset_definition",
                    "custom_preset_item_ids",
                    "update_date",
                ]
            )

            reset_batch_plugin_runs(
                batch,
                preset,
                scope_result["item_ids"],
            )

            run_video_batch_preset.apply_async(
                (
                    batch.id,
                    preset,
                    scope_result["item_ids"],
                    preset_definition,
                )
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

            if batch.plugin_runs.filter(
                status=VideoBatchPluginRun.STATUS_RUNNING
            ).exists():
                return JsonResponse(
                    {"status": "error", "type": "batch_already_running"},
                    status=409,
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
            reset_batch_plugin_runs(
                batch,
                preflight["preset_id"],
                preflight["item_ids"],
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
            # Retry only dependency-failed descendants, never cancelled/deleted
            # work or completed results. Their prerequisites are checked again.
            VideoBatchPluginRun.objects.filter(
                batch=batch, preset=preset,
                status=VideoBatchPluginRun.STATUS_SKIPPED,
                error="dependency_failed",
            ).update(status=VideoBatchPluginRun.STATUS_PENDING, error="")
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
