from django.conf import settings

from data import DataManager
from backend.models import PluginRunResult
from backend.plugin_manager import PluginManager
from backend.utils.analyser_client import TaskAnalyserClient
from backend.utils.parser import Parser
from backend.utils.task import Task


@PluginManager.export_parser("body_pose")
class BodyPoseParser(Parser):
    def __init__(self):
        self.valid_parameter = {
            "fps": {"parser": float, "default": 2},
            "threshold": {"parser": float, "default": 0.8},
        }


@PluginManager.export_plugin("body_pose")
class BodyPoseTask(Task):
    def __call__(self, parameters, video=None, user=None, plugin_run=None, dry_run=False, **kwargs):
        if not 0.1 <= parameters["fps"] <= 25 or not 0 <= parameters["threshold"] <= 1:
            raise ValueError("Invalid body pose FPS or detection threshold")
        manager = DataManager(settings.DATA_OUTPUT_PATH)
        client = TaskAnalyserClient(host=settings.GRPC_HOST, port=settings.GRPC_PORT,
                                    plugin_run_db=plugin_run, manager=manager)
        video_id = self.upload_video(client, video)
        result = self.run_analyser(client, "body_pose", parameters=parameters,
                                   inputs={"video": video_id}, downloads=["poses"])
        if dry_run:
            return result
        if result is None:
            raise ValueError("Body pose analysis failed")
        with result[1]["poses"] as poses:
            saved = PluginRunResult.objects.create(plugin_run=plugin_run, name="body_pose",
                                                   type=PluginRunResult.TYPE_SPATIAL, data_id=poses.id)
        return {"plugin_run": plugin_run.id.hex, "plugin_run_results": [saved.id.hex],
                "timelines": {}, "data": {"poses": poses.id}}
