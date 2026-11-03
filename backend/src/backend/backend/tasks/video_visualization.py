import json
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from backend.models import VideoVisualizationExport
from backend.utils import media_path_to_video
from backend.utils.visualization_export import export_directory
from backend.utils.visualization_render import RenderCancelled, render_video


logger = logging.getLogger(__name__)


@shared_task
def render_visualization_export(job_id):
    jobs = VideoVisualizationExport.objects.filter(id=job_id)
    # Also prevents a redelivery from running the same encoder twice.
    if not jobs.filter(status="queued", cancel_requested=False).update(status="running", updated_at=timezone.now()):
        return
    directory = export_directory(job_id)

    def cancelled():
        return not jobs.filter(status="running", cancel_requested=False).exists()

    def progress(value):
        jobs.filter(status="running").update(progress=value, updated_at=timezone.now())

    try:
        snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        source = media_path_to_video(snapshot["source_file"], snapshot["source_ext"])
        render_video(source, snapshot, directory, progress=progress, cancelled=cancelled,
                     ffmpeg=getattr(settings, "VISUALIZATION_FFMPEG", "ffmpeg"),
                     ffprobe=getattr(settings, "VISUALIZATION_FFPROBE", "ffprobe"))
        if not jobs.filter(status="running", cancel_requested=False).update(status="complete", progress=1, updated_at=timezone.now()):
            raise RenderCancelled()
    except RenderCancelled:
        (directory / "output.mp4").unlink(missing_ok=True)
        jobs.filter(status="running").update(status="cancelled", updated_at=timezone.now())
    except Exception:
        logger.exception("Visualization rendering failed for %s", job_id)
        if cancelled():
            jobs.filter(status="running").update(status="cancelled", updated_at=timezone.now())
        else:
            jobs.update(status="error", error="Rendering failed. Check the worker log and FFmpeg installation, then retry.", updated_at=timezone.now())
