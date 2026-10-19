import io
import json
import uuid
import zipfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from tibava_data import Annotation, AnnotationData, ShotsData

from backend.eaf_filter import filter_eaf_xml
from backend.models import PluginRun, PluginRunResult, Timeline, TimelineSegment, Video, VideoBatch, VideoBatchItem
from backend.tasks.audio_classification import AudioClassification, AudioClassificationParser
from backend.tasks.batch import resolve_step_parameters
from backend.utils.batch_plugin_catalog import batch_metadata_for_plugin
from backend.utils.elan_export import build_elan_archive
from backend.utils.task import PluginRunFailed
from backend.views.video_export import VideoExport


class AudioClassificationMergeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="audio-user")
        self.video = Video.objects.create(owner=self.user, name="clip", ext=".mp4", duration=4)
        self.run = PluginRun.objects.create(video=self.video, type="audio_classification")
        shot_run = PluginRun.objects.create(video=self.video, type="shotdetection")
        shot_result = PluginRunResult.objects.create(
            plugin_run=shot_run, data_id=uuid.uuid4().hex, name="shots", type=PluginRunResult.TYPE_SHOTS,
        )
        self.shots = Timeline.objects.create(video=self.video, name="Shots", plugin_run_result=shot_result)
        TimelineSegment.objects.create(timeline=self.shots, start=0, end=2)
        TimelineSegment.objects.create(timeline=self.shots, start=2, end=4)

    def classify(self, labels):
        task = AudioClassification()
        task.upload_video = Mock(return_value="video-data")
        task.run_analyser = Mock(side_effect=[
            ({"audio": "audio-data"}, {}),
            ({}, {"annotations": AnnotationData(annotations=[
                Annotation(start=0, end=2, labels=labels),
                Annotation(start=2, end=4, labels=[]),
            ])}),
        ])
        manager = Mock()
        manager.create_data.return_value = ShotsData()
        with patch("backend.tasks.audio_classification.DataManager", return_value=manager), patch(
            "backend.tasks.audio_classification.TaskAnalyserClient"
        ) as client:
            client.return_value.upload_data.return_value = "shots-data"
            result = task(
                {"shot_timeline_id": self.shots.id.hex, "timeline": "Audio Classification"},
                video=self.video, user=self.user, plugin_run=self.run,
            )
        return task, result

    def test_eat_contract_and_serializable_multilabel_timeline(self):
        task, result = self.classify(["Speech", "Music"])
        json.dumps(result)
        timeline = Timeline.objects.get(id=result["timelines"]["annotations"])
        self.assertEqual(timeline.video, self.video)
        self.assertEqual(timeline.timelinesegment_set.count(), 2)
        first, second = list(timeline.timelinesegment_set.all())
        self.assertSetEqual(set(first.annotations.values_list("name", flat=True)), {"Speech", "Music"})
        self.assertEqual(second.annotations.count(), 0)
        self.assertEqual(task.run_analyser.call_args.kwargs["inputs"], {"audio": "audio-data", "shots": "shots-data"})
        self.assertEqual(task.run_analyser.call_args.kwargs["parameters"], {"top_k": 3, "threshold": 0.5})

    def test_labels_survive_single_and_batch_elan_export(self):
        self.classify(["Speech", "Music"])
        elan = VideoExport().export_elan({}, self.video)
        self.assertIn("Speech; Music", elan)
        filtered, _ = filter_eaf_xml(elan)
        self.assertIn("Speech; Music", filtered)
        batch = VideoBatch.objects.create(owner=self.user, name="audio")
        item = VideoBatchItem.objects.create(
            batch=batch, video=self.video, original_filename="clip.mp4", original_path="clip.mp4",
        )
        archive = io.BytesIO()
        report = build_elan_archive([item], archive)
        self.assertEqual(report["failed"], [])
        with zipfile.ZipFile(archive) as exported:
            self.assertIn("Speech; Music", exported.read(exported.namelist()[0]).decode())

    def test_invalid_timeline_rejected_before_upload(self):
        other_video = Video.objects.create(owner=self.user, name="other", ext=".mp4")
        other_timeline = Timeline.objects.create(video=other_video, name="Shots")
        task = AudioClassification()
        task.upload_video = Mock()
        for timeline_id in (None, "bad-uuid", uuid.uuid4().hex, other_timeline.id.hex):
            with self.subTest(timeline_id=timeline_id), self.assertRaises(PluginRunFailed):
                task({"shot_timeline_id": timeline_id}, video=self.video, user=self.user, plugin_run=self.run)
        task.upload_video.assert_not_called()

    def test_other_user_cannot_classify_video(self):
        other_user = get_user_model().objects.create(username="other-user")
        with self.assertRaises(PluginRunFailed):
            AudioClassification()({"shot_timeline_id": self.shots.id.hex}, video=self.video, user=other_user)

    def test_empty_shots_rejected_before_upload(self):
        self.shots.timelinesegment_set.all().delete()
        with self.assertRaises(PluginRunFailed):
            AudioClassification()({"shot_timeline_id": self.shots.id.hex}, video=self.video, user=self.user)

    def test_old_segment_mode_is_not_accepted(self):
        self.assertIsNone(AudioClassificationParser()([{"name": "segment_type", "value": "Speaker"}]))
        self.assertIsNone(AudioClassificationParser()([]))

    def test_catalog_supports_both_batch_shot_strategies(self):
        metadata = batch_metadata_for_plugin("audio_classification")
        self.assertEqual(metadata["batch"]["parameter_strategies"]["shot_timeline_id"], ["previous_step_output", "timeline_by_name"])
        item = Mock(video=self.video)
        for strategy, resolution in (
            ("previous_step_output", {"expression": "shotdetection.timelines.shots"}),
            ("timeline_by_name", {"name": "Shots"}),
        ):
            step = {"plugin": "audio_classification", "parameters": [], "parameter_resolution": {
                "shot_timeline_id": {"strategy": strategy, **resolution},
            }}
            result = resolve_step_parameters(step, {"shotdetection": {"timelines": {"shots": self.shots.id.hex}}}, item)
            self.assertEqual(result["status"], "ok")
            self.assertIn({"name": "shot_timeline_id", "value": self.shots.id.hex}, result["parameters"])
