import json
import logging
from backend.plugin_manager import PluginManager

from backend.utils import (
    media_url_to_video,
)
from backend.utils.video_ingest import ingest_video_file

from django.views import View
from django.http import JsonResponse

# from django.core.exceptions import BadRequest

from backend.models import Video


logger = logging.getLogger(__name__)


class VideoUpload(View):
    def submit_analyse(self, plugins, **kwargs):
        plugin_manager = PluginManager()
        for plugin in plugins:
            plugin_manager(plugin, **kwargs)

    def post(self, request):
        try:
            if not request.user.is_authenticated:
                logger.error("VideoUpload::not_authenticated")
                return JsonResponse(
                    {"status": "error", "type": "not_authenticated"}, status=500
                )

            if request.method != "POST":
                logger.error("VideoUpload::wrong_method")
                return JsonResponse(
                    {"status": "error", "type": "database_error"}, status=500
                )
            if "file" in request.FILES:
                ingest_result = ingest_video_file(
                    file=request.FILES["file"],
                    owner=request.user,
                    title=request.POST.get("title"),
                    max_size=request.user.max_video_size,
                )

                if ingest_result["status"] != "ok":
                    logger.error("VideoUpload::failed")
                    return JsonResponse(ingest_result, status=500)

                video_db = ingest_result["video"]
                analyers = request.POST.get("analyser", "").split(",")
                analyers = [x for x in analyers if x]
                self.submit_analyse(
                    plugins=["thumbnail"] + analyers, video=video_db, user=request.user
                )

                return JsonResponse(
                    {
                        "status": "ok",
                        "entries": [
                            {
                                "id": video_db.id.hex,
                                **ingest_result["entry"],
                            }
                        ],
                    }
                )

            return JsonResponse({"status": "error"}, status=500)

        except Exception:
            logger.exception("Video upload by user failed")
            return JsonResponse({"status": "error"}, status=500)


class VideoList(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error"}, status=500)
            entries = []
            for video in Video.objects.filter(owner=request.user):
                entries.append(video.to_dict())
            return JsonResponse({"status": "ok", "entries": entries})
        except Exception as e:
            logger.exception("Error listing videos")
            return JsonResponse({"status": "error"}, status=500)


class VideoGet(View):
    def get(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error"}, status=500)

            entries = []
            for video in Video.objects.filter(id=request.GET.get("id"), owner=request.user):
                video_id_hex = video.id.hex if not video.file else video.file.hex
                entries.append(
                    {
                        **video.to_dict(),
                        "url": media_url_to_video(video_id_hex, video.ext),
                    }
                )
            if len(entries) != 1:
                return JsonResponse({"status": "error"}, status=500)
            return JsonResponse({"status": "ok", "entry": entries[0]})
        except Exception:
            logger.exception("Failed to get video")
            return JsonResponse({"status": "error"}, status=500)


class VideoRename(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error"}, status=500)
            try:
                body = request.body.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                body = request.body

            try:
                data = json.loads(body)
            except Exception as e:
                return JsonResponse({"status": "error"}, status=500)

            if "id" not in data:
                return JsonResponse(
                    {"status": "error", "type": "missing_values"}, status=500
                )
            if "name" not in data:
                return JsonResponse(
                    {"status": "error", "type": "missing_values"}, status=500
                )
            if not isinstance(data.get("name"), str):
                return JsonResponse(
                    {"status": "error", "type": "wrong_request_body"}, status=500
                )

            try:
                video_db = Video.objects.get(id=data.get("id"))
            except Video.DoesNotExist:
                return JsonResponse(
                    {"status": "error", "type": "not_exist"}, status=500
                )

            video_db.name = data.get("name")
            video_db.save()
            return JsonResponse({"status": "ok", "entry": video_db.to_dict()})
        except Exception:
            logger.exception("Failed to rename video")
            return JsonResponse({"status": "error"}, status=500)


class VideoDelete(View):
    def post(self, request):
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error"}, status=500)
            try:
                body = request.body.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                body = request.body

            try:
                data = json.loads(body)
            except Exception as e:
                return JsonResponse({"status": "error"}, status=500)
            count, _ = Video.objects.filter(
                id=data.get("id"), owner=request.user
            ).delete()
            if count:
                return JsonResponse({"status": "ok"})
            return JsonResponse({"status": "error"}, status=500)
        except Exception:
            logger.exception("Failed to delete video")
            return JsonResponse({"status": "error"}, status=500)
