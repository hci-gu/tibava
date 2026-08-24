import hashlib
import os
import posixpath
import uuid
import zipfile
from pathlib import Path

from django.conf import settings

from backend.utils.video_ingest import ALLOWED_VIDEO_EXTENSIONS, is_allowed_video_extension


DEFAULT_MAX_BATCH_FILES = 500
DEFAULT_MAX_BATCH_TOTAL_SIZE = 250 * 1024 * 1024 * 1024
DEFAULT_MAX_ACTIVE_BATCH_INGESTS_PER_USER = 1
DEFAULT_MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH = 1
DEFAULT_MAX_ACTIVE_BATCH_PLUGIN_RUNS_GLOBAL = 4
DEFAULT_MAX_ACTIVE_BATCH_PLUGIN_RUNS_PER_USER = 2


def get_batch_upload_root():
    return Path(getattr(settings, "BATCH_UPLOAD_ROOT", "/tmp/video_batches"))


def get_max_batch_files():
    return getattr(settings, "MAX_BATCH_FILES", DEFAULT_MAX_BATCH_FILES)


def get_max_batch_total_size():
    return getattr(settings, "MAX_BATCH_TOTAL_SIZE", DEFAULT_MAX_BATCH_TOTAL_SIZE)


def get_max_active_batch_ingests_per_user():
    return getattr(
        settings,
        "MAX_ACTIVE_BATCH_INGESTS_PER_USER",
        DEFAULT_MAX_ACTIVE_BATCH_INGESTS_PER_USER,
    )


def get_max_active_plugin_runs_per_batch():
    return getattr(
        settings,
        "MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH",
        DEFAULT_MAX_ACTIVE_PLUGIN_RUNS_PER_BATCH,
    )


def get_max_active_batch_plugin_runs_global():
    return getattr(
        settings,
        "MAX_ACTIVE_BATCH_PLUGIN_RUNS_GLOBAL",
        DEFAULT_MAX_ACTIVE_BATCH_PLUGIN_RUNS_GLOBAL,
    )


def get_max_active_batch_plugin_runs_per_user():
    return getattr(
        settings,
        "MAX_ACTIVE_BATCH_PLUGIN_RUNS_PER_USER",
        DEFAULT_MAX_ACTIVE_BATCH_PLUGIN_RUNS_PER_USER,
    )


def get_batch_dir(batch_id):
    path = get_batch_upload_root() / str(batch_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_path(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            checksum.update(chunk)
    return checksum.hexdigest()


def save_batch_source_file(batch_id, uploaded_file, prefix=None):
    batch_dir = get_batch_dir(batch_id)
    source_dir = batch_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix or uuid.uuid4().hex}{Path(uploaded_file.name).suffix.lower()}"
    output_path = source_dir / filename

    with output_path.open("wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)

    return {
        "path": output_path,
        "file_size": output_path.stat().st_size,
        "checksum": sha256_path(output_path),
    }


def normalize_zip_member_name(name):
    normalized = name.replace("\\", "/")
    normalized = posixpath.normpath(normalized)
    if normalized in {"", "."}:
        return None
    if normalized.startswith("../") or normalized == "..":
        return None
    if normalized.startswith("/"):
        return None
    first_part = normalized.split("/", 1)[0]
    if ":" in first_part:
        return None
    return normalized


def extract_zip_videos(
    zip_path,
    output_dir,
    max_files=None,
    max_total_size=None,
    max_file_size=None,
    allowed_extensions=ALLOWED_VIDEO_EXTENSIONS,
):
    max_files = max_files or get_max_batch_files()
    max_total_size = max_total_size or get_max_batch_total_size()
    max_file_size = max_file_size or getattr(settings, "MAX_BATCH_FILE_SIZE", None)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    valid_count = 0
    total_size = 0

    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            normalized_name = normalize_zip_member_name(info.filename)
            original_filename = Path(info.filename).name

            if info.is_dir():
                continue

            if normalized_name is None:
                entries.append(
                    {
                        "status": "error",
                        "original_filename": original_filename or info.filename,
                        "original_path": info.filename,
                        "file_size": info.file_size,
                        "ingest_error": "unsafe_zip_path",
                    }
                )
                continue

            if not is_allowed_video_extension(normalized_name, allowed_extensions):
                entries.append(
                    {
                        "status": "error",
                        "original_filename": Path(normalized_name).name,
                        "original_path": normalized_name,
                        "file_size": info.file_size,
                        "ingest_error": "wrong_file_extension",
                    }
                )
                continue

            if valid_count >= max_files:
                entries.append(
                    {
                        "status": "error",
                        "original_filename": Path(normalized_name).name,
                        "original_path": normalized_name,
                        "file_size": info.file_size,
                        "ingest_error": "too_many_files",
                    }
                )
                continue

            if max_file_size is not None and info.file_size > max_file_size:
                entries.append(
                    {
                        "status": "error",
                        "original_filename": Path(normalized_name).name,
                        "original_path": normalized_name,
                        "file_size": info.file_size,
                        "ingest_error": "file_too_large",
                    }
                )
                continue

            if total_size + info.file_size > max_total_size:
                entries.append(
                    {
                        "status": "error",
                        "original_filename": Path(normalized_name).name,
                        "original_path": normalized_name,
                        "file_size": info.file_size,
                        "ingest_error": "batch_too_large",
                    }
                )
                continue

            ext = Path(normalized_name).suffix.lower()
            extracted_path = output_dir / f"{uuid.uuid4().hex}{ext}"
            with archive.open(info, "r") as src, extracted_path.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

            valid_count += 1
            total_size += info.file_size
            entries.append(
                {
                    "status": "ok",
                    "original_filename": Path(normalized_name).name,
                    "original_path": normalized_name,
                    "source_path": extracted_path,
                    "file_size": info.file_size,
                    "checksum": sha256_path(extracted_path),
                }
            )

    return entries
