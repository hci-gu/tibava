import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from backend.utils.upload import check_extension, download_file, get_file_extension
from backend.utils.parser import Parser


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
