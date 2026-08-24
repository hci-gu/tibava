import logging
import os
import uuid
from pathlib import Path

import imageio

from backend.models import Video
from backend.utils import get_file_extension, media_dir_to_video, media_url_to_video


logger = logging.getLogger(__name__)


ALLOWED_VIDEO_EXTENSIONS = (".mkv", ".mp4", ".ogv")


class PathUploadFile:
    def __init__(self, path, name=None):
        self.path = Path(path)
        self.name = name or self.path.name
        self.size = self.path.stat().st_size

    def chunks(self, chunk_size=1024 * 1024):
        with self.path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk


def is_allowed_video_extension(filename, extensions=ALLOWED_VIDEO_EXTENSIONS):
    ext = get_file_extension(filename)
    return ext in {allowed.lower() for allowed in extensions}


def save_video_file(file, output_dir, output_name, max_size=None, extensions=None):
    try:
        if extensions is not None and not is_allowed_video_extension(file.name, extensions):
            return {"status": "error", "type": "wrong_file_extension"}

        if max_size is not None and getattr(file, "size", 0) > max_size:
            return {"status": "error", "type": "file_too_large"}

        ext = get_file_extension(file.name)
        os.makedirs(output_dir, exist_ok=True)
        output_path = Path(output_dir) / f"{output_name}{ext}"

        with output_path.open("wb") as f:
            for chunk in file.chunks():
                f.write(chunk)

        return {"status": "ok", "path": output_path, "origin": file.name}
    except Exception:
        logger.exception("Failed to save video file")
        return {"status": "error", "type": "downloading_error"}


def extract_video_metadata(path):
    reader = imageio.get_reader(path)
    metadata = reader.get_meta_data()
    size = metadata["size"]
    return {
        "fps": metadata["fps"],
        "duration": metadata["duration"],
        "width": size[0],
        "height": size[1],
    }


def ingest_video_file(file, owner, title=None, max_size=None, source_metadata=None):
    source_metadata = source_metadata or {}
    video_id_uuid = uuid.uuid4()
    video_id = video_id_uuid.hex
    ext = get_file_extension(file.name)

    save_result = save_video_file(
        output_dir=media_dir_to_video(video_id),
        output_name=video_id,
        file=file,
        max_size=max_size,
        extensions=ALLOWED_VIDEO_EXTENSIONS,
    )
    if save_result["status"] != "ok":
        return save_result

    try:
        meta = {
            "name": title or source_metadata.get("name") or Path(file.name).stem,
            "ext": ext,
            **extract_video_metadata(save_result["path"]),
        }
        video_db = Video.objects.create(
            name=meta["name"],
            id=video_id_uuid,
            file=video_id_uuid,
            ext=meta["ext"],
            fps=meta["fps"],
            duration=meta["duration"],
            width=meta["width"],
            height=meta["height"],
            owner=owner,
        )
    except Exception:
        logger.exception("Failed to ingest video metadata")
        try:
            os.remove(save_result["path"])
        except OSError:
            logger.warning("Failed to remove partially ingested video %s", save_result["path"])
        return {"status": "error", "type": "database_error"}

    return {
        "status": "ok",
        "video": video_db,
        "entry": {
            **video_db.to_dict(),
            "url": media_url_to_video(video_db.file.hex, video_db.ext),
        },
        "path": save_result["path"],
        "origin": save_result["origin"],
    }
