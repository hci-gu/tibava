import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

import backend.tasks
from backend.models import (
    PluginRun,
    PluginRunResult,
    Timeline,
    Video,
    VideoBatch,
    VideoBatchItem,
    VideoBatchPluginRun,
)
from backend.tasks.batch import (
    get_completed_step_outputs,
    ingest_video_batch,
    run_video_batch_preset,
)
from backend.utils import media_url_to_video
from backend.utils.batch_upload import extract_zip_videos, normalize_zip_member_name
from backend.utils.upload import check_extension, download_file, get_file_extension
from backend.utils.parser import Parser
from backend.utils.plugin_presets import (
    build_step_parameters,
    list_batch_presets,
    resolve_dependency,
    validate_batch_preset,
)
from backend.utils.video_ingest import ingest_video_file
from backend.views.video import VideoUpload
from backend.views.video_batch import (
    VideoBatchCancel,
    VideoBatchDelete,
    VideoBatchGet,
    VideoBatchList,
    VideoBatchPresetList,
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

    def test_list_batch_presets_includes_description(self):
        presets = list_batch_presets()

        self.assertTrue(presets)
        self.assertIn("description", presets[0])
        self.assertTrue(presets[0]["description"])

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


class VideoBatchDatabaseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="password",
        )

    def test_refresh_counters_updates_counts_and_preserves_cancelled_status(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Batch")
        VideoBatchItem.objects.create(
            batch=batch,
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        VideoBatchItem.objects.create(
            batch=batch,
            original_filename="b.mp4",
            original_path="folder/b.mp4",
            ingest_status=VideoBatchItem.STATUS_ERROR,
            ingest_error="wrong_file_extension",
        )

        batch.refresh_counters()

        self.assertEqual(batch.total_count, 2)
        self.assertEqual(batch.ready_count, 1)
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(batch.status, VideoBatch.STATUS_PARTIAL_ERROR)

        batch.status = VideoBatch.STATUS_CANCELLED
        batch.refresh_counters()

        self.assertEqual(batch.status, VideoBatch.STATUS_CANCELLED)
        self.assertEqual(batch.failed_count, 1)


class VideoBatchAPIDatabaseTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="api-owner@example.com",
            email="api-owner@example.com",
            password="password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="api-other@example.com",
            email="api-other@example.com",
            password="password",
        )

    def authenticated(self, request, user=None):
        request.user = user or self.user
        return request

    @override_settings(BATCH_UPLOAD_ROOT=tempfile.gettempdir())
    def test_multi_file_batch_upload_persists_paths_and_items(self):
        request = self.authenticated(
            self.factory.post(
                "/video/batch/upload",
                {
                    "name": "Multi",
                    "paths": json.dumps(["folder/a.mp4", "folder/sub/b.mp4"]),
                    "files": [
                        SimpleUploadedFile("a.mp4", b"a"),
                        SimpleUploadedFile("b.mp4", b"b"),
                    ],
                },
            )
        )

        with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
            response = VideoBatchUpload.as_view()(request)

        data = json.loads(response.content)
        batch = VideoBatch.objects.get(id=data["batch_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(batch.items.count(), 2)
        self.assertEqual(
            list(batch.items.order_by("original_path").values_list("original_path", flat=True)),
            ["folder/a.mp4", "folder/sub/b.mp4"],
        )
        enqueue.assert_called_once_with(batch)

    @override_settings(BATCH_UPLOAD_ROOT=tempfile.gettempdir())
    def test_zip_batch_upload_persists_archive_and_enqueues(self):
        request = self.authenticated(
            self.factory.post(
                "/video/batch/upload",
                {
                    "name": "Zip",
                    "zip": SimpleUploadedFile("batch.zip", b"zip"),
                },
            )
        )

        with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
            response = VideoBatchUpload.as_view()(request)

        data = json.loads(response.content)
        batch = VideoBatch.objects.get(id=data["batch_id"])
        self.assertEqual(batch.source_type, VideoBatch.SOURCE_ZIP)
        self.assertTrue(batch.source_path.endswith(".zip"))
        enqueue.assert_called_once_with(batch)

    def test_batch_list_and_detail_are_scoped_to_owner(self):
        own_batch = VideoBatch.objects.create(owner=self.user, name="Own")
        other_batch = VideoBatch.objects.create(owner=self.other_user, name="Other")

        list_response = VideoBatchList.as_view()(
            self.authenticated(self.factory.get("/video/batch/list"))
        )
        entries = json.loads(list_response.content)["entries"]
        self.assertEqual([entry["id"] for entry in entries], [own_batch.id.hex])

        detail_response = VideoBatchGet.as_view()(
            self.authenticated(self.factory.get(f"/video/batch/get?id={other_batch.id.hex}"))
        )
        self.assertEqual(detail_response.status_code, 500)
        self.assertEqual(json.loads(detail_response.content)["type"], "not_exist")

    def test_preset_list_endpoint_returns_descriptions(self):
        response = VideoBatchPresetList.as_view()(
            self.authenticated(self.factory.get("/video/batch/presets"))
        )
        entries = json.loads(response.content)["entries"]

        self.assertTrue(entries)
        self.assertTrue(entries[0]["description"])

    def test_retry_cancel_and_delete_endpoints_update_expected_state(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Actions")
        VideoBatchItem.objects.create(
            batch=batch,
            original_filename="bad.mp4",
            original_path="bad.mp4",
            source_path="/tmp/bad.mp4",
            ingest_status=VideoBatchItem.STATUS_ERROR,
            ingest_error="database_error",
        )

        retry_request = self.authenticated(
            self.factory.post(
                "/video/batch/retry-failed",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
            retry_response = VideoBatchRetryFailed.as_view()(retry_request)
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(batch.items.get().ingest_status, VideoBatchItem.STATUS_PENDING)
        enqueue.assert_called_once_with(batch)

        plugin_run = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=batch.items.get(),
            preset="default_batch_analysis",
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_ERROR,
            error="plugin_run_failed",
        )
        retry_plugins_request = self.authenticated(
            self.factory.post(
                "/video/batch/retry-failed-plugin-steps",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            retry_plugins_response = VideoBatchRetryFailedPluginSteps.as_view()(
                retry_plugins_request
            )
        plugin_run.refresh_from_db()
        self.assertEqual(retry_plugins_response.status_code, 200)
        self.assertEqual(plugin_run.status, VideoBatchPluginRun.STATUS_PENDING)
        run_preset.apply_async.assert_called_once()

        cancel_request = self.authenticated(
            self.factory.post(
                "/video/batch/cancel",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        cancel_response = VideoBatchCancel.as_view()(cancel_request)
        batch.refresh_from_db()
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(batch.status, VideoBatch.STATUS_CANCELLED)

        delete_request = self.authenticated(
            self.factory.post(
                "/video/batch/delete",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        delete_response = VideoBatchDelete.as_view()(delete_request)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(VideoBatch.objects.filter(id=batch.id).exists())


class VideoBatchTaskDatabaseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="task-owner@example.com",
            email="task-owner@example.com",
            password="password",
        )

    def make_video(self, name="Video"):
        return Video.objects.create(
            owner=self.user,
            name=name,
            ext=".mp4",
            fps=25,
            duration=10,
            width=1920,
            height=1080,
        )

    def test_ingest_video_batch_processes_pending_items(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "video.mp4"
            source_path.write_bytes(b"video")
            batch = VideoBatch.objects.create(owner=self.user, name="Ingest")
            item = VideoBatchItem.objects.create(
                batch=batch,
                original_filename="video.mp4",
                original_path="folder/video.mp4",
                source_path=str(source_path),
            )
            video = self.make_video("Ingested")

            with patch("backend.tasks.batch.ingest_video_file") as ingest:
                ingest.return_value = {"status": "ok", "video": video}
                ingest_video_batch(batch.id)

            item.refresh_from_db()
            batch.refresh_from_db()
            self.assertEqual(item.ingest_status, VideoBatchItem.STATUS_READY)
            self.assertEqual(item.video, video)
            self.assertEqual(batch.status, VideoBatch.STATUS_READY)

    def test_run_video_batch_preset_creates_done_step_rows(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Preset")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video(),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        class FakePluginManager:
            def __call__(self, plugin, video, user, parameters, run_async):
                plugin_run = PluginRun.objects.create(
                    video=video,
                    type=plugin,
                    status=PluginRun.STATUS_DONE,
                )
                result = {}
                if plugin == "shotdetection":
                    result = {"timelines": {"shots": "timeline-id"}}
                return {
                    "status": True,
                    "plugin_run": plugin_run.id.hex,
                    "result": result,
                }

        with patch("backend.tasks.batch.PluginManager", return_value=FakePluginManager()):
            run_video_batch_preset(batch.id, "default_batch_analysis")

        batch.refresh_from_db()
        self.assertEqual(batch.status, VideoBatch.STATUS_READY)
        self.assertEqual(
            list(item.plugin_runs.order_by("step_index").values_list("status", flat=True)),
            [
                VideoBatchPluginRun.STATUS_DONE,
                VideoBatchPluginRun.STATUS_DONE,
                VideoBatchPluginRun.STATUS_DONE,
            ],
        )

    def test_retry_preset_reconstructs_timeline_dependency_from_done_step(self):
        video = self.make_video()
        batch = VideoBatch.objects.create(owner=self.user, name="Resume")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=video,
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        thumbnail_run = PluginRun.objects.create(
            video=video,
            type="thumbnail",
            status=PluginRun.STATUS_DONE,
        )
        shot_run = PluginRun.objects.create(
            video=video,
            type="shotdetection",
            status=PluginRun.STATUS_DONE,
        )
        shot_result = PluginRunResult.objects.create(
            plugin_run=shot_run,
            name="shots",
            data_id="shots-data",
            type=PluginRunResult.TYPE_SHOTS,
        )
        timeline = Timeline.objects.create(
            video=video,
            plugin_run_result=shot_result,
            name="Shots",
            type=Timeline.TYPE_PLUGIN_RESULT,
        )
        VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset="default_batch_analysis",
            step_index=0,
            plugin="thumbnail",
            plugin_run=thumbnail_run,
            status=VideoBatchPluginRun.STATUS_DONE,
        )
        shot_tracker = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset="default_batch_analysis",
            step_index=1,
            plugin="shotdetection",
            plugin_run=shot_run,
            status=VideoBatchPluginRun.STATUS_DONE,
        )

        self.assertEqual(
            get_completed_step_outputs(shot_tracker),
            {"timelines": {"shots": timeline.id.hex}},
        )

        calls = []

        class FakePluginManager:
            def __call__(self, plugin, video, user, parameters, run_async):
                calls.append((plugin, parameters))
                plugin_run = PluginRun.objects.create(
                    video=video,
                    type=plugin,
                    status=PluginRun.STATUS_DONE,
                )
                return {
                    "status": True,
                    "plugin_run": plugin_run.id.hex,
                    "result": {},
                }

        with patch("backend.tasks.batch.PluginManager", return_value=FakePluginManager()):
            run_video_batch_preset(batch.id, "default_batch_analysis")

        self.assertEqual(calls[0][0], "shot_type_classification")
        self.assertIn(
            {"name": "shot_timeline_id", "value": timeline.id.hex},
            calls[0][1],
        )
