import json
import logging
import zipfile
from pathlib import Path, PurePosixPath

from backend.eaf_filter import EafFilterError, filter_eaf_xml
from backend.utils.batch_naming import numbered_batch_video_path
from backend.utils.batch_upload import normalize_zip_member_name, repair_filename_unicode


logger = logging.getLogger(__name__)

ELAN_EXPORT_CACHE_TIMEOUT = 60 * 60 * 24


class BatchArchivePathError(ValueError):
    pass


def elan_export_cache_key(job_id):
    return f"elan-export:{job_id}"


def build_elan_archive(
    items, archive_path, progress_callback=None, apply_filtering=True
):
    """Build a batch ELAN archive and report item-level progress while doing so."""
    from backend.views.video_export import ElanExportError, VideoExport

    if not hasattr(archive_path, "write"):
        archive_path = Path(archive_path)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"exported": [], "failed": []}
    used_paths = set()
    exporter = VideoExport()
    item_list = list(items)
    total = len(item_list)
    processed = 0

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in item_list:
            try:
                archive_path_name, linked_file_path = batch_export_paths(item)
                if archive_path_name.casefold() in used_paths:
                    raise BatchArchivePathError("duplicate_video_number")
                elan = exporter.export_elan(
                    {"aggregation": 0},
                    item.video,
                    linked_file_path=linked_file_path,
                )
                if apply_filtering:
                    elan, filter_result = filter_eaf_xml(elan)
                    logger.info(
                        "Filtered batch ELAN export item_id=%s groups=%d "
                        "cluster_groups=%d warnings=%d",
                        item.id.hex,
                        len(filter_result.groups),
                        len(filter_result.cluster_groups),
                        len(filter_result.warnings),
                    )
                archive.writestr(archive_path_name, elan)
                used_paths.add(archive_path_name.casefold())
                report["exported"].append(
                    {"item_id": item.id.hex, "path": archive_path_name}
                )
            except (ElanExportError, BatchArchivePathError) as exc:
                report["failed"].append(
                    {
                        "item_id": item.id.hex,
                        "original_path": repair_filename_unicode(item.original_path),
                        "reason": (
                            exc.code if isinstance(exc, ElanExportError) else str(exc)
                        ),
                    }
                )
            except EafFilterError:
                logger.exception("Failed to filter ELAN for batch item %s", item.id.hex)
                report["failed"].append(
                    {
                        "item_id": item.id.hex,
                        "original_path": repair_filename_unicode(item.original_path),
                        "reason": "eaf_filter_failed",
                    }
                )
            except Exception:
                logger.exception("Failed to export ELAN for batch item %s", item.id.hex)
                report["failed"].append(
                    {
                        "item_id": item.id.hex,
                        "original_path": repair_filename_unicode(item.original_path),
                        "reason": "elan_export_failed",
                    }
                )

            processed += 1
            if progress_callback:
                progress_callback(
                    processed,
                    total,
                    len(report["exported"]),
                    len(report["failed"]),
                    "exporting",
                )

        if progress_callback:
            progress_callback(
                processed,
                total,
                len(report["exported"]),
                len(report["failed"]),
                "finalizing",
            )
        if report["failed"]:
            archive.writestr(
                "export-report.json",
                json.dumps(report, indent=2, ensure_ascii=False),
            )

    return report


def batch_export_paths(item):
    """Return the title-free EAF path and the matching media link.

    Existing batches retain their channel folders and original video names.
    New batches use their canonical display path for both names.
    """
    source = normalize_zip_member_name(item.original_path or item.original_filename)
    display_path = getattr(item, "display_path", "")
    display_path = normalize_zip_member_name(display_path) if display_path else None
    canonical_path = display_path or (
        numbered_batch_video_path(source) if source else None
    )
    if display_path:
        linked_file_path = PurePosixPath(display_path).name
    elif source:
        linked_file_path = PurePosixPath(source).name
    else:
        linked_file_path = repair_filename_unicode(item.original_filename)
    if canonical_path:
        return str(PurePosixPath(canonical_path).with_suffix(".eaf")), linked_file_path
    return f"{item.video_id.hex}.eaf", linked_file_path
