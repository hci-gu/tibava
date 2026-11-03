import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from backend.models import Annotation, PluginRun, PluginRunResult, Timeline, TimelineSegment, Video, VideoVisualizationExport
from backend.tasks.video_visualization import render_visualization_export
from backend.utils.visualization_export import available_sources, build_snapshot, export_directory, validate_configuration
from backend.utils.visualization_render import RenderCancelled, merged_intervals, render_video, safe_text, scalar_intervals


class VisualizationTimingTests(SimpleTestCase):
    def test_scalar_sampling_preserves_gaps_and_clip_offsets(self):
        intervals = scalar_intervals([1, 1.5, 3, 3.5], [2, float("nan"), 8, 9], 0.5, 1.25, 3.75)
        self.assertEqual(intervals, [[0, 0.25, "2"], [1.75, 2.25, "8"], [2.25, 2.5, "9"]])
        self.assertEqual(merged_intervals(intervals, 2.5), [
            [0, 0.25, "2"], [0.25, 1.75, "—"], [1.75, 2.25, "8"], [2.25, 2.5, "9"],
        ])

    def test_single_scalar_does_not_extend_to_end_of_video(self):
        self.assertEqual(scalar_intervals([0], [4], None, 0, 10), [[0, 0.5, "4"]])

    def test_overlapping_annotations_and_exact_boundaries(self):
        self.assertEqual(merged_intervals([[0, 2, "A"], [1, 3, "B"], [3, 4, "C"]], 5), [
            [0, 1, "A"], [1, 2, "A · B"], [2, 3, "B"], [3, 4, "C"], [4, 5, "—"],
        ])

    def test_ass_text_cannot_inject_formatting(self):
        text = safe_text(r"Hello {\p1}\N world", 100, 2)
        self.assertNotIn("{", text)
        self.assertNotIn("\\", text)
        self.assertIn("Hello", text)


class VisualizationApiTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(DATA_CACHE_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create(username="visualization-owner", email="owner@example.test")
        self.other = get_user_model().objects.create(username="visualization-other", email="other@example.test")
        self.video = Video.objects.create(owner=self.user, name="Demo", ext=".mp4", duration=20, fps=30, width=1920, height=1080)
        self.timeline = Timeline.objects.create(video=self.video, name="Transcript", type=Timeline.TYPE_TRANSCRIPT)
        self.segment = TimelineSegment.objects.create(timeline=self.timeline, start=2, end=9)
        self.annotation = Annotation.objects.create(video=self.video, owner=self.user, name="Hello world")
        self.segment.annotations.add(self.annotation)
        self.client.force_login(self.user)

    def configuration(self):
        return {"start": 3, "end": 15, "layers": [{"source_id": f"timeline:{self.timeline.id.hex}", "mode": "caption"}]}

    def post(self, action, data):
        return self.client.post(f"/video/visualization/{action}", data=json.dumps(data), content_type="application/json")

    def make_job(self, status="queued"):
        return VideoVisualizationExport.objects.create(video=self.video, owner=self.user, status=status,
                                                        configuration=self.configuration())

    def test_sources_include_only_completed_scalar_runs(self):
        for status in [PluginRun.STATUS_DONE, PluginRun.STATUS_RUNNING]:
            run = PluginRun.objects.create(video=self.video, type="brightness", status=status)
            PluginRunResult.objects.create(plugin_run=run, name=status, type=PluginRunResult.TYPE_SCALAR)
        sources = available_sources(self.video)
        self.assertEqual([entry["name"] for entry in sources if entry["kind"] == "scalar"], [PluginRun.STATUS_DONE])
        self.assertEqual(sources[-1]["kind"], "caption")

    def test_scalar_classes_use_their_timeline_names(self):
        run = PluginRun.objects.create(video=self.video, type="shot_scale", status=PluginRun.STATUS_DONE)
        parent = Timeline.objects.create(video=self.video, name="Shot scale")
        for name in ["Close up", "Wide"]:
            result = PluginRunResult.objects.create(plugin_run=run, name="shot_scale", type=PluginRunResult.TYPE_SCALAR)
            Timeline.objects.create(video=self.video, parent=parent, name=name, type=Timeline.TYPE_PLUGIN_RESULT, plugin_run_result=result)
        sources = available_sources(self.video)
        self.assertEqual({source["name"] for source in sources if source["kind"] == "scalar"},
                         {"Shot scale / Close up", "Shot scale / Wide"})

    def test_rejects_invalid_ranges_modes_colors_and_foreign_results(self):
        foreign_video = Video.objects.create(owner=self.other, name="Other", ext=".mp4", duration=20)
        foreign = Timeline.objects.create(video=foreign_video, name="Foreign")
        sources = available_sources(self.video)
        for change in [
            {"start": float("nan")}, {"end": 25}, {"start": 5, "end": 4},
            {"layers": [{"source_id": f"timeline:{foreign.id.hex}"}]},
            {"layers": [{"source_id": sources[0]["id"], "mode": "scalar"}]},
            {"layers": [{"source_id": sources[0]["id"], "color": "red;bad"}]},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_configuration(self.video, {**self.configuration(), **change}, sources)

    @patch("backend.views.video_visualization.render_visualization_export.apply_async")
    def test_preview_is_five_seconds_and_freezes_annotations(self, enqueue):
        response = self.post("create", {"video_id": self.video.id.hex, "preview": True, "configuration": self.configuration()})
        self.assertEqual(response.status_code, 202, response.content)
        job = VideoVisualizationExport.objects.get()
        snapshot = json.loads((export_directory(job.id) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["render_end"], 8)
        self.assertEqual(snapshot["layers"][0]["intervals"], [[0, 5, "Hello world"]])
        self.annotation.name = "Changed later"
        self.annotation.save()
        self.assertIn("Hello world", (export_directory(job.id) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(job.configuration["end"], 15)
        enqueue.assert_called_once()

    def test_empty_clip_is_rejected_before_queueing(self):
        configuration = {**self.configuration(), "start": 12}
        response = self.post("create", {"video_id": self.video.id.hex, "configuration": configuration})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VideoVisualizationExport.objects.exists())

    @patch("backend.views.video_visualization.render_visualization_export.apply_async")
    def test_limits_active_jobs(self, enqueue):
        self.make_job()
        self.make_job("running")
        response = self.post("create", {"video_id": self.video.id.hex, "configuration": self.configuration()})
        self.assertEqual(response.status_code, 409)
        enqueue.assert_not_called()

    def test_owner_required_for_sources_status_cancel_and_download(self):
        job = self.make_job("complete")
        self.client.force_login(self.other)
        for action, params in [("sources", {"video_id": self.video.id.hex}), ("status", {"id": job.id.hex}), ("download", {"id": job.id.hex})]:
            response = self.client.get(f"/video/visualization/{action}", params)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.post("cancel", {"id": job.id.hex}).status_code, 404)
        self.assertEqual(self.post("create", {"video_id": self.video.id.hex, "configuration": self.configuration()}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get("/video/visualization/sources").status_code, 403)

    def test_cancel_queued_job_prevents_rendering(self):
        job = self.make_job()
        response = self.post("cancel", {"id": job.id.hex})
        self.assertEqual(response.json()["entry"]["status"], "cancelled")
        with patch("backend.tasks.video_visualization.render_video") as render:
            render_visualization_export(job.id.hex)
        render.assert_not_called()

    def test_running_cancellation_does_not_publish_output(self):
        job = self.make_job()
        directory = export_directory(job.id)
        directory.mkdir(parents=True)
        (directory / "snapshot.json").write_text(json.dumps({"source_file": self.video.file.hex, "source_ext": ".mp4"}))

        def fake_render(*args, **kwargs):
            self.post("cancel", {"id": job.id.hex})
            self.assertTrue(kwargs["cancelled"]())
            (directory / "output.mp4").write_bytes(b"not published")

        with patch("backend.tasks.video_visualization.render_video", side_effect=fake_render):
            render_visualization_export(job.id.hex)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertFalse((directory / "output.mp4").exists())

    def test_worker_success_and_duplicate_delivery(self):
        job = self.make_job()
        directory = export_directory(job.id)
        directory.mkdir(parents=True)
        (directory / "snapshot.json").write_text(json.dumps({"source_file": self.video.file.hex, "source_ext": ".mp4"}))
        with patch("backend.tasks.video_visualization.render_video") as render:
            render_visualization_export(job.id.hex)
            render_visualization_export(job.id.hex)
        render.assert_called_once()
        job.refresh_from_db()
        self.assertEqual((job.status, job.progress), ("complete", 1))

    def test_download_ranges_and_missing_files(self):
        job = self.make_job("complete")
        url = f"/video/visualization/download?id={job.id.hex}"
        self.assertEqual(self.client.get(url).status_code, 410)
        directory = export_directory(job.id)
        directory.mkdir(parents=True)
        (directory / "output.mp4").write_bytes(b"0123456789")
        for requested, expected in [("bytes=2-5", b"2345"), ("bytes=-3", b"789"), ("bytes=7-", b"789")]:
            response = self.client.get(url, HTTP_RANGE=requested)
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b"".join(response.streaming_content), expected)
        self.assertEqual(self.client.get(url, HTTP_RANGE="bytes=30-").status_code, 416)
        response = self.client.get(url + "&download=1")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"0123456789")

    def test_deleting_job_removes_artifacts(self):
        job = self.make_job("complete")
        directory = export_directory(job.id)
        directory.mkdir(parents=True)
        (directory / "output.mp4").write_bytes(b"video")
        job.delete()
        self.assertFalse(directory.exists())


class VisualizationEncoderTests(SimpleTestCase):
    def test_actual_ffmpeg_clip_with_audio_and_silent_portrait(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and ffprobe are required for the encoding integration test")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
            snapshot = {"start": 1, "render_end": 2.5, "max_dimension": 720, "font_scale": 1,
                        "opacity": 0.65, "audio": True, "layers": [
                            {"mode": "scalar", "color": "#FFFF00", "label": "Brightness", "intervals": [[0, 0.5, "0.4"], [1, 1.5, "0.8"]]},
                            {"mode": "caption", "color": "#FFFFFF", "label": "Transcript", "intervals": [[0, 1.5, "Hello world"]]},
                        ]}
            output = render_video(source, snapshot, directory / "render")
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
            self.assertEqual([s["codec_type"] for s in probe["streams"]], ["video", "audio"])
            self.assertAlmostEqual(float(probe["format"]["duration"]), 1.5, delta=0.1)
            # A rendered frame must contain bright overlay pixels, not just blue source pixels.
            frame = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
            self.assertGreater(sum(1 for r, g in zip(frame[0::3], frame[1::3]) if r > 180 and g > 180), 50)
            portrait = directory / "portrait.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=25:d=3", "-c:v", "libx264", str(portrait)], check=True)
            output = render_video(portrait, snapshot, directory / "portrait-render")
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))
            self.assertEqual(len(probe["streams"]), 1)
            self.assertEqual((probe["streams"][0]["width"], probe["streams"][0]["height"]), (180, 320))

    def test_cancellation_before_encoding(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RenderCancelled):
            render_video("unused.mp4", {}, directory, cancelled=lambda: True)
