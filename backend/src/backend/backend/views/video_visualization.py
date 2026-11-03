import json
import logging
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views import View

from backend.models import Video, VideoVisualizationExport
from backend.tasks.video_visualization import render_visualization_export
from backend.utils.visualization_export import (
    available_sources, build_snapshot, export_directory, validate_configuration,
)


logger = logging.getLogger(__name__)


class VisualizationView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"status": "error", "error": "Please sign in."}, status=403)
        try:
            return super().dispatch(request, *args, **kwargs)
        except (Video.DoesNotExist, VideoVisualizationExport.DoesNotExist):
            return JsonResponse({"status": "error", "error": "Video or export not found."}, status=404)
        except (ValueError, TypeError, ValidationError) as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        except Exception:
            logger.exception("Visualization export request failed")
            return JsonResponse({"status": "error", "error": "Could not process the visualization export."}, status=500)

    def owned_job(self, request, job_id):
        return VideoVisualizationExport.objects.get(id=job_id, owner=request.user, video__owner=request.user)


class VideoVisualizationSources(VisualizationView):
    def get(self, request):
        video = Video.objects.get(id=request.GET.get("video_id"), owner=request.user)
        jobs = video.visualization_exports.filter(owner=request.user)[:20]
        return JsonResponse({"status": "ok", "sources": available_sources(video),
                             "jobs": [job.to_dict() for job in jobs]})


class VideoVisualizationCreate(VisualizationView):
    def post(self, request):
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError("Expected export settings.")
        video = Video.objects.get(id=payload.get("video_id"), owner=request.user)
        preview = payload.get("preview", False)
        if not isinstance(preview, bool):
            raise ValueError("Invalid preview option.")
        configuration = validate_configuration(video, payload.get("configuration"), available_sources(video))
        # Freeze values before enqueueing: subsequent annotation edits cannot change this job.
        snapshot = build_snapshot(video, configuration, preview)
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if VideoVisualizationExport.objects.filter(owner=request.user, status__in=["queued", "running"]).count() >= 2:
                return JsonResponse({"status": "error", "error": "Wait for an export to finish, or cancel it first."}, status=409)
            job = VideoVisualizationExport.objects.create(
                owner=request.user, video=video, configuration=configuration, preview=preview,
            )
        try:
            directory = export_directory(job.id)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            render_visualization_export.apply_async(args=[job.id.hex], task_id=f"visualization-{job.id.hex}")
        except Exception:
            logger.exception("Could not queue visualization export %s", job.id)
            job.status = "error"
            job.error = "Could not queue the export. Please try again."
            job.save(update_fields=["status", "error", "updated_at"])
            return JsonResponse({"status": "error", "error": job.error, "entry": job.to_dict()}, status=503)
        return JsonResponse({"status": "ok", "entry": job.to_dict()}, status=202)


class VideoVisualizationStatus(VisualizationView):
    def get(self, request):
        return JsonResponse({"status": "ok", "entry": self.owned_job(request, request.GET.get("id")).to_dict()})


class VideoVisualizationCancel(VisualizationView):
    def post(self, request):
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError("Expected an export ID.")
        job = self.owned_job(request, payload.get("id"))
        # Claiming a queued job and cancelling it must be mutually exclusive.
        VideoVisualizationExport.objects.filter(id=job.id, status="queued").update(
            status="cancelled", cancel_requested=True, updated_at=timezone.now(),
        )
        VideoVisualizationExport.objects.filter(id=job.id, status="running").update(
            cancel_requested=True, updated_at=timezone.now(),
        )
        job.refresh_from_db()
        return JsonResponse({"status": "ok", "entry": job.to_dict()})


def file_chunks(path, start, length):
    with path.open("rb") as handle:
        handle.seek(start)
        while length:
            chunk = handle.read(min(64 * 1024, length))
            if not chunk:
                break
            length -= len(chunk)
            yield chunk


class VideoVisualizationDownload(VisualizationView):
    def get(self, request):
        job = self.owned_job(request, request.GET.get("id"))
        manifest = request.GET.get("manifest") == "1"
        if job.status != "complete":
            return JsonResponse({"status": "error", "error": "The export is not ready."}, status=409)
        path = export_directory(job.id) / ("snapshot.json" if manifest else "output.mp4")
        if not path.is_file():
            return JsonResponse({"status": "error", "error": "This export has expired. Please render it again."}, status=410)
        filename = f"tibava-{'preview' if job.preview else 'visualization'}-{job.id.hex[:8]}.{'json' if manifest else 'mp4'}"
        size = path.stat().st_size
        range_header = request.headers.get("Range") if not manifest else None
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                return JsonResponse({"error": "Invalid byte range."}, status=416, headers={"Content-Range": f"bytes */{size}"})
            left, right = match.groups()
            start = int(left) if left else max(0, size - int(right))
            end = min(size - 1, int(right)) if left and right else size - 1
            if start >= size or end < start:
                return JsonResponse({"error": "Invalid byte range."}, status=416, headers={"Content-Range": f"bytes */{size}"})
            response = StreamingHttpResponse(file_chunks(path, start, end - start + 1), status=206, content_type="video/mp4")
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
            response["Content-Length"] = end - start + 1
        else:
            response = FileResponse(path.open("rb"), content_type="application/json" if manifest else "video/mp4")
        disposition = "attachment" if manifest or request.GET.get("download") == "1" else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "private, no-store"
        return response
