import logging
import uuid

from typing import Dict, List


from backend.models import (
    PluginRun,
    PluginRunResult,
    Video,
    VideoAnalysisState,
    Timeline,
    TimelineSegment,
)
from django.conf import settings
from backend.plugin_manager import PluginManager

from ..utils.analyser_client import TaskAnalyserClient
from data import DataManager
from backend.utils.parser import Parser
from backend.utils.task import Task

from django.db import transaction
from django.conf import settings


@PluginManager.export_parser("shotdetection")
class ShotDetectionParser(Parser):
    def __init__(self):
        self.valid_parameter = {
            "timeline": {"parser": str, "default": "Shots"},
            "fps": {"parser": float, "default": 2.0},
        }


@PluginManager.export_plugin("shotdetection")
class ShotDetection(Task):
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
        plugin_run: PluginRun = None,
        dry_run: bool = False,
        **kwargs,
    ):
        manager = DataManager(self.config["output_path"])
        client = TaskAnalyserClient(
            host=self.config["analyser_host"],
            port=self.config["analyser_port"],
            plugin_run_db=plugin_run,
            manager=manager,
        )

        video_id = self.upload_video(client, video)
        result = self.run_analyser(
            client,
            "transnet_shotdetection",
            parameters={"fps": parameters.get("fps")},
            inputs={"video": video_id},
            downloads=["shots"],
        )

        if result is None:
            raise Exception

        if dry_run or plugin_run is None:
            logging.warning("dry_run or plugin_run is None")
            return {}

        with transaction.atomic():
            with result[1]["shots"] as d:
                plugin_run_result_db = PluginRunResult.objects.create(
                    plugin_run=plugin_run,
                    data_id=d.id,
                    name="shots",
                    type=PluginRunResult.TYPE_SHOTS,
                )

                # TODO translate the name
                timeline = Timeline.objects.create(
                    video=video,
                    name=parameters.get("timeline"),
                    type=Timeline.TYPE_ANNOTATION,
                    plugin_run_result=plugin_run_result_db,
                )
                for shot in d.shots:
                    segment_id = uuid.uuid4().hex
                    timeline_segment = TimelineSegment.objects.create(
                        timeline=timeline,
                        id=segment_id,
                        start=shot.start,
                        end=shot.end,
                    )

                video_analysis_state_db, _ = VideoAnalysisState.objects.get_or_create(
                    video=video
                )
                video_analysis_state_db.selected_shots = timeline
                video_analysis_state_db.save()

                return {
                    "plugin_run": plugin_run.id.hex,
                    "plugin_run_results": [plugin_run_result_db.id.hex],
                    "timelines": {"shots": timeline.id.hex},
                    "data": {"shots": result[1]["shots"].id},
                }
