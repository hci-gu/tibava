import json
import io
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
    TimelineSegment,
    Video,
    VideoAnalysisState,
    VideoBatch,
    VideoBatchItem,
    VideoBatchPluginRun,
)
from backend.plugin_manager import PluginManager
from backend.tasks.batch import (
    get_completed_step_outputs,
    ingest_video_batch,
    resolve_step_parameters,
    run_video_batch_plugin_step,
    run_video_batch_preset,
)
from backend.utils import media_url_to_video
from backend.utils.task import PluginRunFailed, Task
from backend.utils.batch_upload import extract_zip_videos, normalize_zip_member_name
from backend.utils.batch_plugin_catalog import list_batch_plugin_catalog
from backend.utils.upload import check_extension, download_file, get_file_extension
from backend.utils.parser import Parser
from backend.utils.plugin_presets import (
    BATCH_PLUGIN_PRESETS,
    build_step_parameters,
    DEFAULT_BATCH_PRESET,
    list_batch_presets,
    resolve_dependency,
    validate_batch_preset,
    validate_batch_preset_definition,
)
from backend.utils.video_ingest import ingest_video_file
from backend.views.video import VideoUpload
from backend.views.video_batch import (
    VideoBatchCancel,
    VideoBatchDelete,
    VideoBatchExportElan,
    VideoBatchGet,
    VideoBatchList,
    VideoBatchPluginCatalog,
    VideoBatchPresetList,
    VideoBatchRetryFailed,
    VideoBatchRetryFailedPluginSteps,
    VideoBatchRunPluginSet,
    VideoBatchRunPreset,
    VideoBatchSharedInputUpload,
    VideoBatchValidatePluginSet,
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

    def test_extract_zip_videos_ignores_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "batch.zip"
            output_dir = Path(tmp_dir) / "out"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(".DS_Store", b"metadata")
                archive.writestr("notes/readme.txt", b"text")
                archive.writestr("folder/video.mp4", b"video")

            entries = extract_zip_videos(zip_path, output_dir)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["status"], "ok")
            self.assertEqual(entries[0]["original_path"], "folder/video.mp4")

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

    def test_validate_batch_preset_rejects_unknown_dependency(self):
        with patch.dict(
            BATCH_PLUGIN_PRESETS,
            {
                "bad_dependency": {
                    "name": "Bad dependency",
                    "steps": [
                        {
                            "plugin": "thumbnail",
                            "parameters": [],
                            "dependencies": {
                                "shot_timeline_id": "missing.timelines.shots",
                            },
                        }
                    ],
                }
            },
        ):
            result = validate_batch_preset("bad_dependency")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["type"], "invalid_dependency")

    def test_validate_batch_preset_rejects_forward_dependency_cycle(self):
        with patch.dict(
            BATCH_PLUGIN_PRESETS,
            {
                "cycle": {
                    "name": "Cycle",
                    "steps": [
                        {
                            "plugin": "thumbnail",
                            "parameters": [],
                            "dependencies": {
                                "shot_timeline_id": "shotdetection.timelines.shots",
                            },
                        },
                        {
                            "plugin": "shotdetection",
                            "parameters": [
                                {"name": "timeline", "value": "Shots"},
                                {"name": "fps", "value": 2.0},
                            ],
                        },
                    ],
                }
            },
        ):
            result = validate_batch_preset("cycle")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["type"], "dependency_cycle")

    def test_list_batch_presets_excludes_invalid_presets(self):
        with patch.dict(
            BATCH_PLUGIN_PRESETS,
            {
                "bad_dependency": {
                    "name": "Bad dependency",
                    "steps": [
                        {
                            "plugin": "thumbnail",
                            "parameters": [],
                            "dependencies": {
                                "shot_timeline_id": "missing.timelines.shots",
                            },
                        }
                    ],
                }
            },
        ):
            preset_ids = [preset["id"] for preset in list_batch_presets()]

        self.assertIn(DEFAULT_BATCH_PRESET, preset_ids)
        self.assertNotIn("bad_dependency", preset_ids)

    def test_supported_batch_catalog_defaults_validate_against_parsers(self):
        for group in list_batch_plugin_catalog():
            for plugin in group["children"]:
                if not plugin["batch"]["supported"]:
                    continue
                parameters = [
                    {"name": parameter["name"], "value": parameter.get("value")}
                    for parameter in plugin["parameters"] + plugin["optional_parameters"]
                    if parameter.get("value") is not None
                    and not parameter["field"].startswith("select_")
                ]
                step = {"plugin": plugin["plugin"], "parameters": parameters}
                for parameter in plugin["parameters"] + plugin["optional_parameters"]:
                    if parameter["field"] == "select_timeline":
                        step.setdefault("parameter_resolution", {})[
                            parameter["name"]
                        ] = {
                            "strategy": "timeline_by_name",
                            "name": "Shots",
                            "required": True,
                        }
                    if parameter["field"] == "select_scalar_timeline":
                        step.setdefault("parameter_resolution", {})[
                            parameter["name"]
                        ] = {
                            "strategy": "scalar_timeline_by_name",
                            "name": "Scalar",
                            "required": True,
                        }
                    if parameter["field"] == "select_scalar_timelines":
                        step.setdefault("parameter_resolution", {})[
                            parameter["name"]
                        ] = {
                            "strategy": "scalar_timelines_by_name",
                            "names": ["Scalar"],
                            "required": True,
                        }
                    if parameter["field"] in {"image_input", "csv_input"}:
                        step.setdefault("parameter_resolution", {})[
                            parameter["name"]
                        ] = {
                            "strategy": "shared_file",
                            "path": "/tmp/shared-input",
                            "required": True,
                        }

                result = validate_batch_preset_definition(
                    {
                        "name": "Catalog smoke",
                        "description": "",
                        "steps": [step],
                    }
                )
                self.assertEqual(result["status"], "ok", plugin["plugin"])

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
                    SimpleUploadedFile(".DS_Store", b"metadata"),
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
        request_user = SimpleNamespace(is_authenticated=True)
        fake_batch = SimpleNamespace(
            id=batch_id,
            owner=request_user,
            preset="default_batch_analysis",
        )
        request = RequestFactory().post(
            "/video/batch/retry-failed-plugin-steps",
            data=f'{{"id":"{batch_id.hex}"}}',
            content_type="application/json",
        )
        request.user = request_user

        with patch("backend.views.video_batch.VideoBatch.objects.get") as get_batch:
            with patch("backend.views.video_batch.VideoBatchPluginRun.objects.filter") as filter_steps:
                with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
                    with patch(
                        "backend.views.video_batch.user_has_active_batch_capacity",
                        return_value=True,
                    ):
                        get_batch.return_value = fake_batch
                        filter_steps.return_value.update.return_value = 1

                        response = VideoBatchRetryFailedPluginSteps.as_view()(request)

        self.assertEqual(response.status_code, 200)
        run_preset.apply_async.assert_called_once_with(
            (batch_id, "default_batch_analysis", None, None)
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

    def create_ready_batch_item(self, batch, original_path, with_shots=True):
        video = Video.objects.create(
            owner=batch.owner,
            name=Path(original_path).stem,
            ext=Path(original_path).suffix,
        )
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=video,
            original_filename=Path(original_path).name,
            original_path=original_path,
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        if with_shots:
            shots = Timeline.objects.create(
                video=video,
                name="Shots",
                type=Timeline.TYPE_ANNOTATION,
            )
            TimelineSegment.objects.create(timeline=shots, start=0, end=1)
            VideoAnalysisState.objects.create(video=video, selected_shots=shots)
        return item

    @override_settings(BATCH_UPLOAD_ROOT=tempfile.gettempdir())
    def test_multi_file_batch_upload_persists_paths_and_items(self):
        request = self.authenticated(
            self.factory.post(
                "/video/batch/upload",
                {
                    "name": "Multi",
                    "paths": json.dumps(["folder/a.mp4", "folder/sub/b.mp4", ".DS_Store"]),
                    "files": [
                        SimpleUploadedFile("a.mp4", b"a"),
                        SimpleUploadedFile("b.mp4", b"b"),
                        SimpleUploadedFile(".DS_Store", b"metadata"),
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

    @override_settings(BATCH_UPLOAD_ROOT=tempfile.gettempdir())
    def test_batch_upload_defaults_auto_run_to_default_preset(self):
        request = self.authenticated(
            self.factory.post(
                "/video/batch/upload",
                {
                    "name": "Auto preset",
                    "auto_run_preset": "true",
                    "files": [SimpleUploadedFile("a.mp4", b"a")],
                },
            )
        )

        with patch("backend.views.video_batch.enqueue_batch_ingest"):
            response = VideoBatchUpload.as_view()(request)

        data = json.loads(response.content)
        batch = VideoBatch.objects.get(id=data["batch_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(batch.preset, DEFAULT_BATCH_PRESET)
        self.assertTrue(batch.auto_run_preset)

    def test_batch_upload_rejects_unknown_preset(self):
        request = self.authenticated(
            self.factory.post(
                "/video/batch/upload",
                {
                    "name": "Bad preset",
                    "preset": "missing_preset",
                    "files": [SimpleUploadedFile("a.mp4", b"a")],
                },
            )
        )

        with patch("backend.views.video_batch.enqueue_batch_ingest") as enqueue:
            response = VideoBatchUpload.as_view()(request)

        data = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["type"], "not_exist")
        self.assertFalse(VideoBatch.objects.filter(name="Bad preset").exists())
        enqueue.assert_not_called()

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

    def test_batch_elan_export_preserves_folders_and_resolves_name_collisions(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Research batch")
        self.create_ready_batch_item(batch, "group/session/clip.mp4")
        self.create_ready_batch_item(batch, "group/session/clip.mov")

        request = self.authenticated(
            self.factory.post(
                "/video/batch/export-elan",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        response = VideoBatchExportElan.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("research-batch-elan.zip", response["Content-Disposition"])
        self.assertEqual(response["X-Exported-Count"], "2")
        self.assertEqual(response["X-Failed-Count"], "0")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(
                archive.namelist(),
                [
                    "group/session/clip.eaf",
                    "group/session/clip (2).eaf",
                ],
            )
            elan_files = [
                archive.read("group/session/clip.eaf").decode("utf-8"),
                archive.read("group/session/clip (2).eaf").decode("utf-8"),
            ]
            self.assertTrue(all("ANNOTATION_DOCUMENT" in elan for elan in elan_files))
            self.assertTrue(any("clip.mp4" in elan for elan in elan_files))
            self.assertTrue(any("clip.mov" in elan for elan in elan_files))

    def test_batch_elan_export_includes_report_for_partial_failures(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Partial")
        successful = self.create_ready_batch_item(batch, "ok/video.mp4")
        failed = self.create_ready_batch_item(
            batch, "missing/video.mp4", with_shots=False
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/export-elan",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )
        response = VideoBatchExportElan.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Exported-Count"], "1")
        self.assertEqual(response["X-Failed-Count"], "1")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("ok/video.eaf", archive.namelist())
            report = json.loads(archive.read("export-report.json"))
        self.assertEqual(report["exported"][0]["item_id"], successful.id.hex)
        self.assertEqual(
            report["failed"],
            [
                {
                    "item_id": failed.id.hex,
                    "original_path": "missing/video.mp4",
                    "reason": "missing_shot_timeline",
                }
            ],
        )

    def test_batch_elan_export_is_scoped_to_owner(self):
        batch = VideoBatch.objects.create(owner=self.other_user, name="Private")
        self.create_ready_batch_item(batch, "video.mp4")
        request = self.authenticated(
            self.factory.post(
                "/video/batch/export-elan",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        response = VideoBatchExportElan.as_view()(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content)["type"], "not_exist")

    def test_batch_elan_export_replaces_unsafe_archive_paths(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Unsafe path")
        item = self.create_ready_batch_item(batch, "../video.mp4")
        request = self.authenticated(
            self.factory.post(
                "/video/batch/export-elan",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        response = VideoBatchExportElan.as_view()(request)

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), [f"{item.video_id.hex}.eaf"])

    def test_preset_list_endpoint_returns_descriptions(self):
        response = VideoBatchPresetList.as_view()(
            self.authenticated(self.factory.get("/video/batch/presets"))
        )
        entries = json.loads(response.content)["entries"]

        self.assertTrue(entries)
        self.assertTrue(entries[0]["description"])

    def test_plugin_catalog_endpoint_returns_supported_and_disabled_plugins(self):
        response = VideoBatchPluginCatalog.as_view()(
            self.authenticated(self.factory.get("/video/batch/plugin-catalog"))
        )

        data = json.loads(response.content)
        plugins = {
            plugin["plugin"]: plugin
            for group in data["entries"]
            for plugin in group["children"]
        }
        self.assertEqual(response.status_code, 200)
        self.assertTrue(plugins["thumbnail"]["batch"]["supported"])
        self.assertTrue(plugins["insightface_identification"]["batch"]["supported"])
        self.assertEqual(
            plugins["insightface_identification"]["batch"]["file_inputs"],
            "shared",
        )
        self.assertTrue(plugins["clip_ontology"]["batch"]["supported"])
        self.assertEqual(plugins["clip_ontology"]["parameters"][2]["name"], "concept_csv")

    def test_shared_input_upload_returns_reusable_batch_path(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Shared input")

        with tempfile.TemporaryDirectory() as tmp_dir, self.settings(BATCH_UPLOAD_ROOT=tmp_dir):
            request = self.authenticated(
                self.factory.post(
                    "/video/batch/shared-input/upload",
                    {
                        "id": batch.id.hex,
                        "file": SimpleUploadedFile(
                            "ontology.csv",
                            b"label,prompt\ncar,a car\n",
                            content_type="text/csv",
                        ),
                    },
                )
            )
            response = VideoBatchSharedInputUpload.as_view()(request)

            data = json.loads(response.content)
            output_path = Path(data["entry"]["path"])

            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["entry"]["origin"], "ontology.csv")
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.read_bytes(), b"label,prompt\ncar,a car\n")
            self.assertEqual(output_path.parent.name, "shared_inputs")

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

    def test_cancel_rejects_completed_batch(self):
        batch = VideoBatch.objects.create(
            owner=self.user,
            name="Completed",
            status=VideoBatch.STATUS_READY,
        )
        request = self.authenticated(
            self.factory.post(
                "/video/batch/cancel",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        response = VideoBatchCancel.as_view()(request)

        batch.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["type"], "batch_not_cancellable")
        self.assertEqual(batch.status, VideoBatch.STATUS_READY)

    def test_run_preset_rejects_when_user_has_another_active_batch(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Ready")
        VideoBatch.objects.create(
            owner=self.user,
            name="Already running",
            status=VideoBatch.STATUS_RUNNING,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/run-preset",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRunPreset.as_view()(request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content)["type"], "too_many_active_batches")
        run_preset.apply_async.assert_not_called()

    def test_run_preset_accepts_selected_item_scope(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Scoped")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="B", ext=".mp4"),
            original_filename="b.mp4",
            original_path="folder/b.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/run-preset",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {
                            "type": "item_ids",
                            "item_ids": [item.id.hex],
                        },
                    }
                ),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRunPreset.as_view()(request)

        self.assertEqual(response.status_code, 200)
        _, args, _ = run_preset.apply_async.mock_calls[0]
        self.assertEqual(args[0][1], DEFAULT_BATCH_PRESET)
        self.assertEqual(args[0][2], [item.id])

    def test_run_preset_preserves_current_custom_definition_and_restarts_rows(self):
        custom_preset = {
            "name": "Custom thumbnails",
            "description": "Custom plugin set",
            "steps": [{"plugin": "thumbnail", "parameters": []}],
        }
        batch = VideoBatch.objects.create(
            owner=self.user,
            name="Custom rerun",
            status=VideoBatch.STATUS_READY,
            preset="custom:thumbnails",
            custom_preset_definition=custom_preset,
        )
        item = self.create_ready_batch_item(batch, "video.mp4")
        previous = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset=batch.preset,
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_DONE,
        )
        request = self.authenticated(
            self.factory.post(
                "/video/batch/run-preset",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRunPreset.as_view()(request)

        batch.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VideoBatchPluginRun.objects.filter(id=previous.id).exists())
        self.assertEqual(batch.custom_preset_definition, custom_preset)
        self.assertEqual(batch.custom_preset_item_ids, [item.id.hex])
        run_preset.apply_async.assert_called_once_with(
            (batch.id, batch.preset, [item.id], custom_preset)
        )

    def test_run_preset_restarts_completed_rows_for_builtin_preset(self):
        batch = VideoBatch.objects.create(
            owner=self.user,
            name="Built-in rerun",
            status=VideoBatch.STATUS_READY,
            preset=DEFAULT_BATCH_PRESET,
        )
        item = self.create_ready_batch_item(batch, "video.mp4")
        previous = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset=DEFAULT_BATCH_PRESET,
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_DONE,
        )
        request = self.authenticated(
            self.factory.post(
                "/video/batch/run-preset",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRunPreset.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(VideoBatchPluginRun.objects.filter(id=previous.id).exists())
        run_preset.apply_async.assert_called_once_with(
            (batch.id, DEFAULT_BATCH_PRESET, [item.id], None)
        )

    def test_run_plugin_set_dispatches_custom_preset_for_folder_scope(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Custom")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="B", ext=".mp4"),
            original_filename="b.mp4",
            original_path="other/b.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/run-plugin-set",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {
                            "type": "folder",
                            "folder_path": "folder",
                            "include_subfolders": True,
                        },
                        "steps": [{"plugin": "thumbnail", "parameters": []}],
                    }
                ),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRunPluginSet.as_view()(request)

        data = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["item_count"], 1)
        _, args, _ = run_preset.apply_async.mock_calls[0]
        self.assertEqual(args[0][2], [item.id])
        self.assertEqual(args[0][3]["steps"][0]["plugin"], "thumbnail")

    def test_validate_plugin_set_returns_preflight_summary(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Validate")
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/validate-plugin-set",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {"type": "all"},
                        "steps": [
                            {
                                "plugin": "audio_rms",
                                "parameters": [
                                    {"name": "timeline", "value": "Audio RMS"},
                                    {"name": "sr", "value": 8000},
                                ],
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )

        response = VideoBatchValidatePluginSet.as_view()(request)
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["runnable_count"], 1)
        self.assertEqual(data["step_count"], 1)
        self.assertEqual(data["total_jobs"], 1)

    def test_validate_plugin_set_rejects_unsupported_plugin(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Unsupported")
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/validate-plugin-set",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {"type": "all"},
                        "steps": [{"plugin": "insightface_identification"}],
                    }
                ),
                content_type="application/json",
            )
        )

        with patch("backend.utils.batch_plugin_catalog.PluginManager") as plugin_manager:
            plugin_manager.return_value.__contains__.return_value = False
            response = VideoBatchValidatePluginSet.as_view()(request)
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["type"], "plugin_not_batch_supported")

    def test_validate_plugin_set_rejects_missing_required_parameter(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Missing parameter")
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/validate-plugin-set",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {"type": "all"},
                        "steps": [
                            {
                                "plugin": "clip",
                                "parameters": [
                                    {"name": "timeline", "value": "CLIP"}
                                ],
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )

        response = VideoBatchValidatePluginSet.as_view()(request)
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["type"], "invalid_parameters")

    def test_validate_plugin_set_reports_missing_timeline_skip(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Missing timeline")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/validate-plugin-set",
                data=json.dumps(
                    {
                        "id": batch.id.hex,
                        "scope": {"type": "all"},
                        "steps": [
                            {
                                "plugin": "shot_type_classification",
                                "parameters": [
                                    {"name": "timeline", "value": "Camera Setting"},
                                    {"name": "fps", "value": 2},
                                ],
                                "parameter_resolution": {
                                    "shot_timeline_id": {
                                        "strategy": "timeline_by_name",
                                        "name": "Shots",
                                        "required": True,
                                    }
                                },
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )

        response = VideoBatchValidatePluginSet.as_view()(request)
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["type"], "no_runnable_items")
        self.assertEqual(
            data["skipped_items"],
            [{"item_id": item.id.hex, "reason": "missing_required_timeline"}],
        )

    def test_retry_custom_plugin_set_preserves_original_scope(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Retry custom")
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="A", ext=".mp4"),
            original_filename="a.mp4",
            original_path="folder/a.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        VideoBatchItem.objects.create(
            batch=batch,
            video=Video.objects.create(owner=self.user, name="B", ext=".mp4"),
            original_filename="b.mp4",
            original_path="folder/b.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        batch.preset = "custom:retry"
        batch.custom_preset_definition = {
            "name": "Retry",
            "description": "Custom plugin set",
            "steps": [{"plugin": "thumbnail", "parameters": []}],
        }
        batch.custom_preset_item_ids = [item.id.hex]
        batch.save()
        VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset=batch.preset,
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_ERROR,
            error="plugin_run_failed",
        )

        request = self.authenticated(
            self.factory.post(
                "/video/batch/retry-failed-plugin-steps",
                data=json.dumps({"id": batch.id.hex}),
                content_type="application/json",
            )
        )

        with patch("backend.views.video_batch.run_video_batch_preset") as run_preset:
            response = VideoBatchRetryFailedPluginSteps.as_view()(request)

        self.assertEqual(response.status_code, 200)
        _, args, _ = run_preset.apply_async.mock_calls[0]
        self.assertEqual(args[0][1], "custom:retry")
        self.assertEqual(args[0][2], [item.id.hex])
        self.assertEqual(args[0][3], batch.custom_preset_definition)


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

    def test_ingest_video_batch_auto_runs_preset_for_partial_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "video.mp4"
            source_path.write_bytes(b"video")
            batch = VideoBatch.objects.create(
                owner=self.user,
                name="Auto preset",
                preset=DEFAULT_BATCH_PRESET,
                auto_run_preset=True,
            )
            VideoBatchItem.objects.create(
                batch=batch,
                original_filename="video.mp4",
                original_path="video.mp4",
                source_path=str(source_path),
            )
            VideoBatchItem.objects.create(
                batch=batch,
                original_filename="bad.txt",
                original_path="bad.txt",
                ingest_status=VideoBatchItem.STATUS_ERROR,
                ingest_error="wrong_file_extension",
            )
            video = self.make_video("Ingested")

            with patch("backend.tasks.batch.ingest_video_file") as ingest:
                with patch("backend.tasks.batch.run_video_batch_preset.apply_async") as run_preset:
                    ingest.return_value = {"status": "ok", "video": video}
                    ingest_video_batch(batch.id)

            batch.refresh_from_db()
            self.assertEqual(batch.status, VideoBatch.STATUS_PARTIAL_ERROR)
            self.assertEqual(batch.ready_count, 1)
            run_preset.assert_called_once_with((batch.id, DEFAULT_BATCH_PRESET))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
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
                    shot_result = PluginRunResult.objects.create(
                        plugin_run=plugin_run,
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
                    result = {"timelines": {"shots": timeline.id.hex}}
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

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
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

    def test_resolve_step_parameters_finds_timeline_by_name(self):
        video = self.make_video()
        item = VideoBatchItem.objects.create(
            batch=VideoBatch.objects.create(owner=self.user, name="Timeline"),
            video=video,
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        timeline = Timeline.objects.create(
            video=video,
            name="Shots",
            type=Timeline.TYPE_PLUGIN_RESULT,
        )
        step = {
            "plugin": "shot_type_classification",
            "parameters": [
                {"name": "timeline", "value": "Camera Setting"},
                {"name": "fps", "value": 2},
            ],
            "parameter_resolution": {
                "shot_timeline_id": {
                    "strategy": "timeline_by_name",
                    "name": "Shots",
                    "required": True,
                }
            },
        }

        result = resolve_step_parameters(step, {}, item)

        self.assertEqual(result["status"], "ok")
        self.assertIn(
            {"name": "shot_timeline_id", "value": timeline.id.hex},
            result["parameters"],
        )

    def test_resolve_step_parameters_skips_missing_timeline_by_name(self):
        item = VideoBatchItem.objects.create(
            batch=VideoBatch.objects.create(owner=self.user, name="Timeline"),
            video=self.make_video(),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        step = {
            "plugin": "shot_type_classification",
            "parameters": [{"name": "timeline", "value": "Camera Setting"}],
            "parameter_resolution": {
                "shot_timeline_id": {
                    "strategy": "timeline_by_name",
                    "name": "Shots",
                    "required": True,
                }
            },
        }

        result = resolve_step_parameters(step, {}, item)

        self.assertEqual(result, {"status": "skip", "type": "missing_required_timeline"})

    def test_resolve_step_parameters_reuses_shared_file_path(self):
        item = VideoBatchItem.objects.create(
            batch=VideoBatch.objects.create(owner=self.user, name="Shared file"),
            video=self.make_video(),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        step = {
            "plugin": "clip_ontology",
            "parameters": [{"name": "timeline", "value": "CLIP Ontology"}],
            "parameter_resolution": {
                "concept_csv": {
                    "strategy": "shared_file",
                    "path": "/tmp/shared/ontology.csv",
                    "required": True,
                }
            },
        }

        result = resolve_step_parameters(step, {}, item)

        self.assertEqual(result["status"], "ok")
        self.assertIn(
            {"name": "concept_csv", "path": "/tmp/shared/ontology.csv"},
            result["parameters"],
        )

    def test_resolve_step_parameters_maps_multiple_scalar_timelines_by_name(self):
        video = self.make_video()
        item = VideoBatchItem.objects.create(
            batch=VideoBatch.objects.create(owner=self.user, name="Scalar timelines"),
            video=video,
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        plugin_run = PluginRun.objects.create(
            video=video,
            type="clip",
            status=PluginRun.STATUS_DONE,
        )
        first_result = PluginRunResult.objects.create(
            plugin_run=plugin_run,
            name="first",
            data_id="first-data",
            type=PluginRunResult.TYPE_SCALAR,
        )
        second_result = PluginRunResult.objects.create(
            plugin_run=plugin_run,
            name="second",
            data_id="second-data",
            type=PluginRunResult.TYPE_SCALAR,
        )
        first_timeline = Timeline.objects.create(
            video=video,
            plugin_run_result=first_result,
            name="First",
            type=Timeline.TYPE_PLUGIN_RESULT,
        )
        second_timeline = Timeline.objects.create(
            video=video,
            plugin_run_result=second_result,
            name="Second",
            type=Timeline.TYPE_PLUGIN_RESULT,
        )
        step = {
            "plugin": "aggregate_scalar",
            "parameters": [
                {"name": "timeline", "value": "Aggregate Scalar"},
                {"name": "aggregation", "value": 0},
            ],
            "parameter_resolution": {
                "timeline_ids": {
                    "strategy": "scalar_timelines_by_name",
                    "names": ["First", "Second"],
                    "required": True,
                }
            },
        }

        result = resolve_step_parameters(step, {}, item)

        self.assertEqual(result["status"], "ok")
        self.assertIn(
            {
                "name": "timeline_ids",
                "value": [first_timeline.id.hex, second_timeline.id.hex],
            },
            result["parameters"],
        )

    @override_settings(MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH=1)
    def test_batch_scheduler_respects_per_batch_parallelism_limit(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Limited")
        for name in ["a.mp4", "b.mp4"]:
            VideoBatchItem.objects.create(
                batch=batch,
                video=self.make_video(name),
                original_filename=name,
                original_path=name,
                ingest_status=VideoBatchItem.STATUS_READY,
            )

        with patch("backend.tasks.batch.run_video_batch_plugin_step.apply_async") as apply_async:
            run_video_batch_preset(batch.id, "default_batch_analysis")

        self.assertEqual(
            VideoBatchPluginRun.objects.filter(
                batch=batch,
                status=VideoBatchPluginRun.STATUS_RUNNING,
            ).count(),
            1,
        )
        apply_async.assert_called_once()

    @override_settings(MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH=4)
    def test_batch_scheduler_limits_rows_to_scoped_items(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Scoped scheduler")
        selected_item = VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video("Selected"),
            original_filename="selected.mp4",
            original_path="folder/selected.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        unselected_item = VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video("Unselected"),
            original_filename="unselected.mp4",
            original_path="folder/unselected.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        with patch("backend.tasks.batch.run_video_batch_plugin_step.apply_async"):
            run_video_batch_preset(
                batch.id,
                "default_batch_analysis",
                item_ids=[selected_item.id],
            )

        self.assertEqual(selected_item.plugin_runs.count(), 3)
        self.assertEqual(unselected_item.plugin_runs.count(), 0)

    @override_settings(MAX_ACTIVE_BATCH_PLUGIN_RUNS_GLOBAL=1)
    def test_batch_scheduler_respects_global_plugin_backpressure(self):
        other_batch = VideoBatch.objects.create(owner=self.user, name="Other")
        other_item = VideoBatchItem.objects.create(
            batch=other_batch,
            video=self.make_video("Other"),
            original_filename="other.mp4",
            original_path="other.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        VideoBatchPluginRun.objects.create(
            batch=other_batch,
            item=other_item,
            preset="default_batch_analysis",
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_RUNNING,
        )
        batch = VideoBatch.objects.create(owner=self.user, name="Backpressure")
        VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video("Video"),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        with patch("backend.tasks.batch.run_video_batch_plugin_step.apply_async") as apply_async:
            run_video_batch_preset(batch.id, "default_batch_analysis")

        self.assertFalse(
            VideoBatchPluginRun.objects.filter(
                batch=batch,
                status=VideoBatchPluginRun.STATUS_RUNNING,
            ).exists()
        )
        apply_async.assert_not_called()

    def test_batch_scheduler_marks_deleted_video_items(self):
        batch = VideoBatch.objects.create(owner=self.user, name="Deleted video")
        VideoBatchItem.objects.create(
            batch=batch,
            video=None,
            original_filename="deleted.mp4",
            original_path="deleted.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )

        run_video_batch_preset(batch.id, "default_batch_analysis")

        item = batch.items.get()
        item.refresh_from_db()
        self.assertEqual(item.ingest_status, VideoBatchItem.STATUS_ERROR)
        self.assertEqual(item.ingest_error, "video_deleted")

    def test_plugin_manager_returns_failure_type_for_plugin_run_failed(self):
        video = self.make_video()

        class FailingPlugin:
            def __call__(self, parameters, **kwargs):
                raise PluginRunFailed("video_decode_failed")

        previous_plugin = PluginManager._plugins.get("failing_test_plugin")
        PluginManager._plugins["failing_test_plugin"] = FailingPlugin
        try:
            result = PluginManager()(
                "failing_test_plugin",
                video=video,
                user=self.user,
                run_async=False,
            )
        finally:
            if previous_plugin is None:
                del PluginManager._plugins["failing_test_plugin"]
            else:
                PluginManager._plugins["failing_test_plugin"] = previous_plugin

        self.assertFalse(result["status"])
        self.assertEqual(result["type"], "video_decode_failed")
        plugin_run = PluginRun.objects.get(id=result["plugin_run"])
        self.assertEqual(plugin_run.status, PluginRun.STATUS_ERROR)

    def test_batch_plugin_step_records_plugin_failure_type(self):
        batch = VideoBatch.objects.create(
            owner=self.user,
            name="Plugin failure type",
            status=VideoBatch.STATUS_RUNNING,
        )
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video(),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        tracker = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset="default_batch_analysis",
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_RUNNING,
        )

        class FakePluginManager:
            def __call__(self, plugin, video, user, parameters, run_async):
                plugin_run = PluginRun.objects.create(
                    video=video,
                    type=plugin,
                    status=PluginRun.STATUS_ERROR,
                )
                return {
                    "status": False,
                    "type": "video_decode_failed",
                    "plugin_run": plugin_run.id.hex,
                }

        with patch("backend.tasks.batch.PluginManager", return_value=FakePluginManager()):
            with patch("backend.tasks.batch.run_video_batch_preset.apply_async"):
                run_video_batch_plugin_step(
                    tracker.id,
                    batch.id,
                    "default_batch_analysis",
                )

        tracker.refresh_from_db()
        self.assertEqual(tracker.status, VideoBatchPluginRun.STATUS_ERROR)
        self.assertEqual(tracker.error, "video_decode_failed")
        self.assertIsNotNone(tracker.plugin_run)

    def test_batch_plugin_step_respects_mid_run_cancellation(self):
        batch = VideoBatch.objects.create(
            owner=self.user,
            name="Cancel during plugin",
            status=VideoBatch.STATUS_RUNNING,
        )
        item = VideoBatchItem.objects.create(
            batch=batch,
            video=self.make_video(),
            original_filename="video.mp4",
            original_path="video.mp4",
            ingest_status=VideoBatchItem.STATUS_READY,
        )
        tracker = VideoBatchPluginRun.objects.create(
            batch=batch,
            item=item,
            preset="default_batch_analysis",
            step_index=0,
            plugin="thumbnail",
            status=VideoBatchPluginRun.STATUS_RUNNING,
        )

        class FakePluginManager:
            def __call__(self, plugin, video, user, parameters, run_async):
                batch.status = VideoBatch.STATUS_CANCELLED
                batch.save(update_fields=["status", "update_date"])
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
            run_video_batch_plugin_step(
                tracker.id,
                batch.id,
                "default_batch_analysis",
            )

        tracker.refresh_from_db()
        self.assertEqual(tracker.status, VideoBatchPluginRun.STATUS_SKIPPED)
        self.assertEqual(tracker.error, "cancelled")

    def test_task_upload_video_caches_analyser_data_id(self):
        video = self.make_video()
        client = Mock()
        client.upload_file.return_value = "analyser-video-id"

        data_id = Task().upload_video(client, video)

        video.refresh_from_db()
        self.assertEqual(data_id, "analyser-video-id")
        self.assertEqual(video.analyser_data_id, "analyser-video-id")
        self.assertEqual(video.analyser_data_file, video.file)
        self.assertEqual(video.analyser_data_ext, video.ext)
        client.upload_file.assert_called_once()

    def test_task_upload_video_reuses_cached_analyser_data_id(self):
        video = self.make_video()
        video.analyser_data_id = "cached-analyser-video-id"
        video.analyser_data_file = video.file
        video.analyser_data_ext = video.ext
        video.save()
        client = Mock()

        data_id = Task().upload_video(client, video)

        self.assertEqual(data_id, "cached-analyser-video-id")
        client.upload_file.assert_not_called()
