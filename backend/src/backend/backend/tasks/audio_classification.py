from typing import Dict
import math

from ..utils.analyser_client import TaskAnalyserClient

from backend.plugin_manager import PluginManager

from backend.utils.parser import Parser
from backend.utils.task import Task, PluginRunFailed
from backend.utils.color import get_color_from_label

from tibava_data import DataManager, Shot
from backend.models import (
    Annotation,
    PluginRun,
    PluginRunResult,
    TimelineSegmentAnnotation,
    Video,
    TibavaUser,
    Timeline,
    TimelineSegment,
)
from django.db import transaction
from django.db.models import Q
from django.conf import settings
from django.core.exceptions import ValidationError


@PluginManager.export_parser("audio_classification")
class AudioClassificationParser(Parser):
    def __init__(self):
        self.valid_parameter = {
            "timeline": {"parser": str, "default": "Audio Classification"},
            "shot_timeline_id": {"parser": str, "required": True},
            "top_k": {"parser": int, "default": 3},
            "threshold": {"parser": float, "default": 0.5},
        }


@PluginManager.export_plugin("audio_classification")
class AudioClassification(Task):
    def __init__(self):
        self.config = {
            "output_path": "/predictions/",
            "analyser_host": settings.GRPC_HOST,
            "analyser_port": settings.GRPC_PORT,
        }

    def __call__(
        self,
        parameters: Dict,
        video: Video = None,
        user: TibavaUser = None,
        plugin_run: PluginRun = None,
        dry_run: bool = False,
        **kwargs,
    ):
        if not user or not video or video.owner_id != user.id:
            raise PluginRunFailed("invalid_shot_timeline")
        try:
            shot_timeline = Timeline.objects.get(
                Q(plugin_run_result__type=PluginRunResult.TYPE_SHOTS) | Q(name__iexact="Shots"),
                id=parameters.get("shot_timeline_id"),
                video=video,
                video__owner=user,
                type=Timeline.TYPE_ANNOTATION,
            )
        except (Timeline.DoesNotExist, ValueError, TypeError, ValidationError):
            raise PluginRunFailed("invalid_shot_timeline")
        segments = list(TimelineSegment.objects.filter(timeline=shot_timeline))
        if not segments or any(
            not math.isfinite(x.start) or not math.isfinite(x.end) or x.start < 0 or x.end <= x.start
            for x in segments
        ):
            raise PluginRunFailed("invalid_shot_segments")
        if not 1 <= parameters.get("top_k", 3) <= 527 or not 0 <= parameters.get("threshold", 0.5) <= 1:
            raise PluginRunFailed("invalid_audio_parameters")

        manager = DataManager(self.config["output_path"])
        client = TaskAnalyserClient(
            host=self.config["analyser_host"],
            port=self.config["analyser_port"],
            plugin_run_db=plugin_run,
            manager=manager,
        )

        video_id = self.upload_video(client, video)
        audio_result = self.run_analyser(
            client,
            "video_to_audio",
            inputs={"video": video_id},
            outputs=["audio"],
        )

        if plugin_run is not None:
            plugin_run.progress = 0.3
            plugin_run.save()

        if audio_result is None:
            raise Exception

        shots = manager.create_data("ShotsData")
        with shots:
            for segment in segments:
                shots.shots.append(Shot(start=segment.start, end=segment.end))
        shots_id = client.upload_data(shots)
        if not shots_id:
            raise PluginRunFailed("shot_upload_failed")

        result = self.run_analyser(
            client,
            "audio_classification",
            inputs={
                "audio": audio_result[0]["audio"],
                "shots": shots_id,
            },
            parameters={
                "top_k": parameters.get("top_k", 3),
                "threshold": parameters.get("threshold", 0.5),
            },
            downloads=["annotations"],
        )

        if result is None:
            raise PluginRunFailed("audio_classification_failed")

        if dry_run or plugin_run is None:
            return {}

        with transaction.atomic():
            with result[1]["annotations"] as data:

                timeline = Timeline.objects.create(
                    video=video,
                    name=parameters.get("timeline"),
                    type=Timeline.TYPE_ANNOTATION,
                )
                for annotation in data.annotations:
                    timeline_segment_db = TimelineSegment.objects.create(
                        timeline=timeline,
                        start=annotation.start,
                        end=annotation.end,
                    )
                    for label in annotation.labels:
                        if len(label) > settings.ANNOTATION_MAX_LENGTH:
                            label = (
                                label[: max(0, settings.ANNOTATION_MAX_LENGTH - 4)]
                                + " ..."
                            )
                        annotation_db, _ = Annotation.objects.get_or_create(
                            name=label,
                            video=video,
                            owner=user,
                            color=get_color_from_label(label),
                        )

                        TimelineSegmentAnnotation.objects.create(
                            annotation=annotation_db,
                            timeline_segment=timeline_segment_db,
                        )

            return {
                "plugin_run": plugin_run.id.hex,
                "plugin_run_results": [],
                "timelines": {"annotations": timeline.id.hex},
                "data": {
                    "annotations": result[1]["annotations"].id,
                },
            }
