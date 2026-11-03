"""COCO person keypoints, stored in decoded-frame normalized coordinates."""

from pathlib import Path

from data import Annotation, AnnotationData, VideoData
from inference_ray.plugin import AnalyserPlugin, AnalyserPluginManager
from utils import VideoDecoder


@AnalyserPluginManager.export("body_pose")
class BodyPose(
    AnalyserPlugin,
    config={"model_file": "/models/body_pose/keypointrcnn_resnet50_fpn_coco-fc266e95.pth", "model_device": "cpu"},
    parameters={"fps": 2, "threshold": 0.8}, version="0.1",
    requires={"video": VideoData}, provides={"poses": AnnotationData},
):
    def __init__(self, config=None, **kwargs):
        super().__init__(None, **kwargs)
        self._config = {**self._default_config, **(config or {})}
        self.model = None

    def load_model(self):
        path = Path(self.config["model_file"])
        if not path.is_file():
            raise ValueError("Body pose model is missing. Run scripts/download-body-pose-model.py on the server first.")
        import torch
        from torchvision.models.detection import keypointrcnn_resnet50_fpn
        if self.model is None:
            self.model = keypointrcnn_resnet50_fpn(weights=None, weights_backbone=None)
            self.model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
            self.model.to(self.config["model_device"]).eval()

    def call(self, inputs, data_manager, parameters=None, callbacks=None):
        import torch

        fps, threshold = float(parameters["fps"]), float(parameters["threshold"])
        if not 0.1 <= fps <= 25 or not 0 <= threshold <= 1:
            raise ValueError("Body pose requires FPS between 0.1 and 25 and threshold between 0 and 1")
        self.load_model()
        with inputs["video"] as video, video.open_video() as video_file, data_manager.create_data("AnnotationData") as output:
            decoder = VideoDecoder(path=video_file, fps=fps, max_dimension=1280, extension=f".{video.ext}")
            for frame in decoder:
                pixels = frame["frame"]
                height, width = pixels.shape[:2]
                tensor = torch.from_numpy(pixels.copy()).permute(2, 0, 1).float().div(255).to(self.config["model_device"])
                with torch.inference_mode():
                    prediction = self.model([tensor])[0]
                values = {key: value.detach().cpu().tolist() for key, value in prediction.items()}
                for index, confidence in enumerate(values["scores"]):
                    if confidence < threshold:
                        continue
                    x1, y1, x2, y2 = values["boxes"][index]
                    points = [[x / width, y / height, int(score > 2)]
                              for (x, y, _), score in zip(values["keypoints"][index], values["keypoints_scores"][index])]
                    output.annotations.append(Annotation(
                        start=float(frame["time"]), end=float(frame["time"]) + 1 / fps,
                        labels=[{"bbox": [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height],
                                 "confidence": confidence, "keypoints": points}],
                    ))
                self.update_callbacks(callbacks, progress=min(0.99, frame["time"] / max(decoder.duration(), 0.01)))
            self.update_callbacks(callbacks, progress=1.0)
            return {"poses": output}
