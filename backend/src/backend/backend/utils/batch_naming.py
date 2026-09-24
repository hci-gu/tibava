"""Names shared by batch folder display and ELAN exports."""

import re
from pathlib import PurePosixPath

from django.utils.text import slugify

from backend.utils.batch_upload import normalize_zip_member_name


MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
RANKED_TITLE = re.compile(r"(0?[1-9]|1[0-9]|20)(?: - .+)?\Z")


def numbered_batch_video_path(path, slug_channel=False):
    """Map .../<channel>/<month>/<rank> - <title>.<ext> to rank.<ext>.

    Leading folders are retained. Existing channel names stay unchanged unless
    this is a new upload, for which the channel folder is slugged.
    """
    source = normalize_zip_member_name(path)
    if not source:
        return None
    parts = source.split("/")
    if len(parts) < 3 or not MONTH.fullmatch(parts[-2]):
        return None
    filename = PurePosixPath(parts[-1])
    match = RANKED_TITLE.fullmatch(filename.stem)
    if not match or not filename.suffix:
        return None
    if slug_channel:
        channel = slugify(parts[-3])
        if not channel:
            return None
        parts[-3] = channel
    parts[-1] = f"{int(match.group(1)):02d}{filename.suffix.lower()}"
    return "/".join(parts)
