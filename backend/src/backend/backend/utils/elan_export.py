import json
import logging
import zipfile
from pathlib import Path

from backend.eaf_filter import EafFilterError, filter_eaf_xml
from backend.utils.batch_upload import normalize_zip_member_name, repair_filename_unicode


logger = logging.getLogger(__name__)

ELAN_EXPORT_CACHE_TIMEOUT = 60 * 60 * 24


def elan_export_cache_key(job_id):
    return f"elan-export:{job_id}"


def build_elan_archive(
    items, archive_path, progress_callback=None, apply_filtering=True
):
    """Build a batch ELAN archive and report item-level progress while doing so."""
    from backend.views.video_export import ElanExportError, VideoExport

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
            archive_path_name = _archive_path(item, used_paths)
            try:
                normalized_source_path = normalize_zip_member_name(
                    item.original_path or item.original_filename
                )
                linked_file_path = (
                    Path(normalized_source_path).name
                    if normalized_source_path
                    else repair_filename_unicode(item.original_filename)
                )
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
                report["exported"].append(
                    {"item_id": item.id.hex, "path": archive_path_name}
                )
            except ElanExportError as exc:
                report["failed"].append(
                    {
                        "item_id": item.id.hex,
                        "original_path": repair_filename_unicode(item.original_path),
                        "reason": exc.code,
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


def _archive_path(item, used_paths):
    normalized_path = normalize_zip_member_name(
        item.original_path or item.original_filename
    )
    if normalized_path is None:
        normalized_path = f"{item.video_id.hex}.eaf"
    else:
        normalized_path = str(Path(normalized_path).with_suffix(".eaf")).replace(
            "\\", "/"
        )

    candidate = normalized_path
    index = 2
    while candidate.casefold() in used_paths:
        path = Path(normalized_path)
        candidate = str(path.with_name(f"{path.stem} ({index}){path.suffix}")).replace(
            "\\", "/"
        )
        index += 1
    used_paths.add(candidate.casefold())
    return candidate
