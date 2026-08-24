import tempfile
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

import backend.tasks
from backend.utils import media_url_to_video
from backend.utils.batch_upload import extract_zip_videos, normalize_zip_member_name
from backend.utils.upload import check_extension, download_file, get_file_extension
from backend.utils.parser import Parser
from backend.utils.plugin_presets import (
    build_step_parameters,
    resolve_dependency,
    validate_batch_preset,
)
from backend.utils.video_ingest import ingest_video_file
from backend.views.video import VideoUpload
from backend.views.video_batch import (
    VideoBatchCancel,
    VideoBatchGet,
    VideoBatchRetryFailed,
    VideoBatchRetryFailedPluginSteps,
    VideoBatchUpload,
)


class ParserDefaultTests(SimpleTestCase):
    def test_preserves_falsy_defaults(self):
        parser = Parser()
        parser.valid_parameter = {
            "none": {"default": None},
            "false": {"default": False},
            "zero": {"default": 0},
            "empty": {"default": ""},
        }

        self.assertEqual(
            parser([]),
            {"none": None, "false": False, "zero": 0, "empty": ""},
        )


class UploadExtensionTests(SimpleTestCase):
    def test_get_file_extension_uses_final_suffix_and_normalizes_case(self):
        self.assertEqual(get_file_extension("Föreläsning åäö 01.MP4"), ".mp4")
        self.assertEqual(get_file_extension("Föreläsning åäö 01.final.MP4"), ".mp4")

    def test_check_extension_allows_supported_video_filenames(self):
        extensions = (".mkv", ".mp4", ".ogv")

        self.assertTrue(check_extension("Föreläsning åäö 01.MP4", extensions))
        self.assertTrue(check_extension("Föreläsning åäö 01.final.mp4", extensions))
        self.assertFalse(check_extension("Föreläsning åäö 01.mov", extensions))

    def test_download_file_accepts_supported_filename_characters(self):
        uploaded_file = SimpleUploadedFile(
            "Föreläsning åäö 01.final.MP4",
            b"video bytes",
            content_type="video/mp4",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = download_file(
                file=uploaded_file,
                output_dir=tmp_dir,
                output_name="video-id",
                extensions=(".mkv", ".mp4", ".ogv"),
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["origin"], "Föreläsning åäö 01.final.MP4")
            self.assertEqual(result["path"], Path(tmp_dir) / "video-id.mp4")
            self.assertEqual(result["path"].read_bytes(), b"video bytes")


class BatchZipTests(SimpleTestCase):
    def test_normalize_zip_member_name_rejects_unsafe_paths(self):
        self.assertIsNone(normalize_zip_member_name("../escape.mp4"))
        self.assertIsNone(normalize_zip_member_name("/absolute.mp4"))
        self.assertIsNone(normalize_zip_member_name("C:/absolute.mp4"))
        self.assertEqual(
            normalize_zip_member_name("folder/../video.mp4"),
            "video.mp4",
        )

    def test_extract_zip_videos_reports_malicious_paths(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "batch.zip"
            output_dir = Path(tmp_dir) / "out"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../escape.mp4", b"video")

            entries = extract_zip_videos(zip_path, output_dir)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "error")
            self.assertEqual(entries[0]["ingest_error"], "unsafe_zip_path")
            self.assertEqual(list(output_dir.glob("*")), [])

    def test_extract_zip_videos_reports_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "batch.zip"
            output_dir = Path(tmp_dir) / "out"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("notes/readme.txt", b"text")

            entries = extract_zip_videos(zip_path, output_dir)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "error")
            self.assertEqual(entries[0]["original_path"], "notes/readme.txt")
            self.assertEqual(entries[0]["ingest_error"], "wrong_file_extension")

    def test_extract_zip_videos_preserves_folder_paths_for_valid_videos(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "batch.zip"
            output_dir = Path(tmp_dir) / "out"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("folder/sub/video.mp4", b"video")

            entries = extract_zip_videos(zip_path, output_dir)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "ok")
            self.assertEqual(entries[0]["original_filename"], "video.mp4")
            self.assertEqual(entries[0]["original_path"], "folder/sub/video.mp4")
            self.assertTrue(entries[0]["source_path"].exists())

    def test_extract_zip_videos_raises_for_malformed_archives(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "batch.zip"
            zip_path.write_bytes(b"not a zip")

            with self.assertRaises(zipfile.BadZipFile):
                extract_zip_videos(zip_path, Path(tmp_dir) / "out")


class BatchPresetTests(SimpleTestCase):
    def test_default_batch_preset_validates(self):
        self.assertEqual(validate_batch_preset()["status"], "ok")

    def test_resolve_dependency_reads_nested_step_outputs(self):
        outputs = {"shotdetection": {"timelines": {"shots": "timeline-id"}}}

        self.assertEqual(
            resolve_dependency("shotdetection.timelines.shots", outputs),
            "timeline-id",
        )

    def test_build_step_parameters_adds_resolved_dependency(self):
        step = {
            "parameters": [{"name": "timeline", "value": "Camera Setting"}],
            "dependencies": {"shot_timeline_id": "shotdetection.timelines.shots"},
        }
        outputs = {"shotdetection": {"timelines": {"shots": "timeline-id"}}}

        self.assertEqual(
            build_step_parameters(step, outputs),
            [
                {"name": "timeline", "value": "Camera Setting"},
                {"name": "shot_timeline_id", "value": "timeline-id"},
            ],
        )


class VideoIngestHelperTests(SimpleTestCase):
    def test_ingest_video_file_creates_video_from_saved_file_and_metadata(self):
        video_id = uuid.uuid4()
        fake_video = SimpleNamespace(
            id=video_id,
            file=video_id,
            ext=".mp4",
            to_dict=lambda: {"id": video_id.hex, "name": "Lecture"},
        )
        uploaded_file = SimpleUploadedFile("lecture.mp4", b"video")
        owner = SimpleNamespace(max_video_size=1024)

        with patch("backend.utils.video_ingest.save_video_file") as save_file:
            with patch("backend.utils.video_ingest.extract_video_metadata") as metadata:
                with patch("backend.utils.video_ingest.Video.objects.create") as create:
                    save_file.return_value = {
                        "status": "ok",
                        "path": Path("lecture.mp4"),
                        "origin": "lecture.mp4",
                    }
                    metadata.return_value = {
                        "fps": 25,
                        "duration": 10,
                        "width": 1920,
                        "height": 1080,
                    }
                    create.return_value = fake_video

                    result = ingest_video_file(
                        uploaded_file,
                        owner=owner,
                        title="Lecture",
                        max_size=owner.max_video_size,
                    )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["video"], fake_video)
        self.assertEqual(
            result["entry"]["url"],
            media_url_to_video(video_id.hex, ".mp4"),
        )
        create.assert_called_once()


class VideoUploadViewTests(SimpleTestCase):
    def test_video_upload_uses_ingest_result_and_starts_default_plugins(self):
        video_id = uuid.uuid4()
        fake_video = SimpleNamespace(
            id=video_id,
            file=video_id,
            ext=".mp4",
            to_dict=lambda: {"id": video_id.hex, "name": "Lecture"},
        )
        request = RequestFactory().post(
            "/video/upload",
            {
                "title": "Lecture",
                "analyser": "shotdetection",
                "file": SimpleUploadedFile("lecture.mp4", b"video"),
            },
        )
        request.user = SimpleNamespace(is_authenticated=True, max_video_size=1024)

        with patch("backend.views.video.ingest_video_file") as ingest:
            with patch.object(VideoUpload, "submit_analyse") as submit_analyse:
                ingest.return_value = {
                    "status": "ok",
                    "video": fake_video,
                    "entry": {
                        "id": video_id.hex,
                        "name": "Lecture",
                        "url": media_url_to_video(video_id.hex, ".mp4"),
                    },
                }

                response = VideoUpload.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(video_id.hex.encode("utf-8"), response.content)
        submit_analyse.assert_called_once_with(
            plugins=["thumbnail", "shotdetection"],
            video=fake_video,
            user=request.user,
        )


class VideoBatchViewTests(SimpleTestCase):
    def make_authenticated_request(self, data=None, files=None):
        request = RequestFactory().post("/video/batch/upload", data or {}, FILES=files)
        request.user = SimpleNamespace(is_authenticated=True, max_video_size=1024)
        return request

    def test_multi_file_batch_upload_creates_items_and_enqueues_ingest(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(id=batch_id, refresh_counters=Mock())
        request = RequestFactory().post(
            "/video/batch/upload",
            {
                "name": "Batch",
                "files": [
                    SimpleUploadedFile("a.mp4", b"a"),
                    SimpleUploadedFile("b.mp4", b"b"),
                ],
            },
        )
        request.user = SimpleNamespace(is_authenticated=True, max_video_size=1024)

        with patch("backend.views.video_batch.VideoBatch.objects") as batches:
            with patch("backend.views.video_batch.VideoBatchItem.objects.create") as create_item:
                with patch("backend.views.video_batch.save_batch_source_file") as save_source:
                    with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
                        batches.filter.return_value.count.return_value = 0
                        batches.create.return_value = fake_batch
                        save_source.side_effect = [
                            {
                                "path": Path("a.mp4"),
                                "file_size": 1,
                                "checksum": "checksum-a",
                            },
                            {
                                "path": Path("b.mp4"),
                                "file_size": 1,
                                "checksum": "checksum-b",
                            },
                        ]

                        response = VideoBatchUpload.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(batch_id.hex.encode("utf-8"), response.content)
        self.assertEqual(create_item.call_count, 2)
        enqueue.assert_called_once_with(fake_batch)

    def test_zip_batch_upload_saves_archive_and_enqueues_ingest(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(id=batch_id, save=Mock())
        request = RequestFactory().post(
            "/video/batch/upload",
            {
                "name": "Zip Batch",
                "zip": SimpleUploadedFile("batch.zip", b"zip"),
            },
        )
        request.user = SimpleNamespace(is_authenticated=True, max_video_size=1024)

        with patch("backend.views.video_batch.VideoBatch.objects") as batches:
            with patch("backend.views.video_batch.save_batch_source_file") as save_source:
                with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
                    batches.filter.return_value.count.return_value = 0
                    batches.create.return_value = fake_batch
                    save_source.return_value = {
                        "path": Path("batch.zip"),
                        "file_size": 3,
                        "checksum": "checksum",
                    }

                    response = VideoBatchUpload.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(batch_id.hex.encode("utf-8"), response.content)
        self.assertEqual(fake_batch.source_path, "batch.zip")
        enqueue.assert_called_once_with(fake_batch)

    def test_batch_get_scopes_lookup_to_authenticated_owner(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(
            to_dict=Mock(return_value={"id": batch_id.hex, "items": []})
        )
        request = RequestFactory().get(f"/video/batch/get?id={batch_id.hex}")
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("backend.views.video_batch.VideoBatch.objects.get") as get_batch:
            get_batch.return_value = fake_batch

            response = VideoBatchGet.as_view()(request)

        self.assertEqual(response.status_code, 200)
        get_batch.assert_called_once_with(id=batch_id.hex, owner=request.user)

    def test_retry_failed_ingest_items_enqueues_batch(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(id=batch_id)
        request = RequestFactory().post(
            "/video/batch/retry-failed",
            data=f'{{"id":"{batch_id.hex}"}}',
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("backend.views.video_batch.VideoBatch.objects.get") as get_batch:
            with patch("backend.views.video_batch.VideoBatchItem.objects.filter") as filter_items:
                with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
                    get_batch.return_value = fake_batch
                    filter_items.return_value.exclude.return_value.exclude.return_value.update.return_value = 1

                    response = VideoBatchRetryFailed.as_view()(request)

        self.assertEqual(response.status_code, 200)
        enqueue.assert_called_once_with(fake_batch)

    def test_retry_failed_plugin_steps_enqueues_preset(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(id=batch_id, preset="default_batch_analysis")
        request = RequestFactory().post(
            "/video/batch/retry-failed-plugin-steps",
            data=f'{{"id":"{batch_id.hex}"}}',
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("backend.views.video_batch.VideoBatch.objects.get") as get_batch:
            with patch("backend.views.video_batch.VideoBatchPluginRun.objects.filter") as filter_steps:
                with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
                    get_batch.return_value = fake_batch
                    filter_steps.return_value.update.return_value = 1

                    response = VideoBatchRetryFailedPluginSteps.as_view()(request)

        self.assertEqual(response.status_code, 200)
        run_preset.apply_async.assert_called_once_with(
            (batch_id, "default_batch_analysis")
        )

    def test_cancel_batch_marks_batch_cancelled(self):
        batch_id = uuid.uuid4()
        fake_batch = SimpleNamespace(id=batch_id)
        request = RequestFactory().post(
            "/video/batch/cancel",
            data=f'{{"id":"{batch_id.hex}"}}',
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with patch("backend.views.video_batch.VideoBatch.objects.get") as get_batch:
            with patch("backend.views.video_batch.cancel_batch_work") as cancel_work:
                get_batch.return_value = fake_batch

                response = VideoBatchCancel.as_view()(request)

        self.assertEqual(response.status_code, 200)
        cancel_work.assert_called_once_with(fake_batch)
