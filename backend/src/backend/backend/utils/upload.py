import os

from pathlib import Path
import PIL
import requests
from urllib.parse import unquote

import cgi
import mimetypes
import logging


logger = logging.getLogger(__name__)


def get_file_extension(filename: Path | str):
    if isinstance(filename, str):
        filename = Path(filename)
    return filename.suffix.lower()


def check_extension(filename: Path, extensions: list):
    extension = get_file_extension(filename)
    allowed_extensions = {allowed_extension.lower() for allowed_extension in extensions}
    return extension in allowed_extensions


def download_file(file, output_dir, output_name=None, max_size=None, extensions=None):
    try:
        path = Path(file.name)
        ext = get_file_extension(path)
        if output_name is not None:
            output_path = os.path.join(output_dir, f"{output_name}{ext}")
        else:
            output_path = os.path.join(output_dir, f"{file.name}")

        if extensions is not None:
            if not check_extension(path, extensions):
                return {"status": "error", "type": "wrong_file_extension"}
        # TODO add parameter
        if max_size is not None:
            if file.size > max_size:
                return {"status": "error", "type": "file_too_large"}

        os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "wb") as f:

            for i, chunk in enumerate(file.chunks()):
                f.write(chunk)

        return {"status": "ok", "path": Path(output_path), "origin": file.name}
    except Exception:
        logger.exception("Failed to download file")
        return {"status": "error", "type": "downloading_error"}


def download_url(url, output_dir, output_name=None, max_size=None, extensions=None):
    try:
        response = requests.get(url, stream=True)
        if response.status_code != 200:
            return {"status": "error", "type": "downloading_error"}

        params = cgi.parse_header(response.headers.get("Content-Disposition", ""))[-1]
        if "filename" in params:
            filename = os.path.basename(params["filename"])
            ext = get_file_extension(filename)
            if extensions is not None:
                if not check_extension(filename, extensions):

                    return {
                        "status": "error",
                        "type": "wrong_file_extension",
                    }

        elif response.headers.get("Content-Type") != None:

            ext = mimetypes.guess_extension(response.headers.get("Content-Type"))
            if ext is None:
                return {"status": "error", "type": "downloading_error"}

            if extensions is not None:
                if ext.lower() not in {
                    allowed_extension.lower() for allowed_extension in extensions
                }:
                    return {
                        "status": "error",
                        "type": "wrong_file_extension",
                    }
            filename = url
        else:
            return {"status": "error", "type": "file_not_found"}

        if output_name is not None:
            output_path = os.path.join(output_dir, f"{output_name}{ext}")
        else:
            output_path = os.path.join(output_dir, f"{filename}")

        size = 0
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(1024):
                size += 1024

                if size > max_size:
                    return {"status": "error", "type": "file_too_large"}
                f.write(chunk)

        return {"status": "ok", "path": Path(output_path), "origin": filename}
    except:
        return {"status": "error", "type": "downloading_error"}
