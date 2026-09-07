#!/usr/bin/env python3
"""Remove low-confidence EAF labels and their discovered score tiers."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


CONFIDENCE_THRESHOLD = 0.5
VALUE_PREFIXES_TO_STRIP = (
    "Transcript:",
    "Audio Gender:",
    "Emotion:",
    "Shot Size:",
    "Shot Scale:",
    "Shot Movement:",
    "Sentiment:",
)
SHOT_SEGMENT_TIER_ID = "Shots"
SHOT_BOUNDARY_SOURCE_TIER_ID = "Shot"
PLAIN_NUMERIC_VALUE_TIER_IDS = (SHOT_SEGMENT_TIER_ID, "Audio RMS")
MERGED_TRANSCRIPT_TIER_ID = "Transcript"
CONSOLIDATED_OCR_TIER_ID = "OCR"

_XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


class EafFilterError(Exception):
    """Raised when an EAF cannot be filtered safely."""


@dataclass(frozen=True)
class AnnotationRecord:
    wrapper: ET.Element
    annotation: ET.Element
    interval: tuple[int, int]
    value: str


@dataclass(frozen=True)
class TierInfo:
    element: ET.Element
    tier_id: str
    records: tuple[AnnotationRecord, ...]
    contains_only_alignable_annotations: bool


@dataclass
class GroupResult:
    main_tier: str
    score_tiers: list[str]
    kept: int = 0
    filtered: int = 0
    warnings: int = 0


@dataclass
class FilterResult:
    output_path: Path | None
    groups: list[GroupResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    removed_time_slots: int = 0
    stripped_value_prefixes: int = 0
    created_shot_segments: int = 0
    transcript_chunks_combined: int = 0
    original_ocr_annotations: int = 0
    consolidated_ocr_windows: int = 0


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _descendants(element: ET.Element, name: str) -> Iterable[ET.Element]:
    return (node for node in element.iter() if _local_name(node.tag) == name)


def _annotation_value(annotation: ET.Element) -> str:
    values = _children(annotation, "ANNOTATION_VALUE")
    if len(values) != 1:
        raise EafFilterError(
            "Every annotation must contain exactly one ANNOTATION_VALUE element."
        )
    return values[0].text or ""


def _parse_number(text: str) -> float:
    stripped = text.strip()
    if not _NUMBER_RE.fullmatch(stripped):
        raise ValueError("not a decimal number")
    value = float(stripped)
    if not math.isfinite(value):
        raise ValueError("not a finite number")
    return value


def parse_confidence(text: str) -> float:
    """Parse an EAF confidence value and normalize it to the range 0..1."""

    stripped = text.strip()
    is_percentage = stripped.endswith("%")
    if is_percentage:
        stripped = stripped[:-1].strip()
    elif stripped.startswith("value:"):
        stripped = stripped[len("value:") :].strip()

    value = _parse_number(stripped)
    if is_percentage:
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        raise ValueError("confidence is outside the range 0..1")
    return value


def _looks_like_confidence(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("value:") or stripped.endswith("%"):
        return True
    try:
        _parse_number(stripped)
    except ValueError:
        return False
    return True


def _label_variants(value: str) -> tuple[str, ...]:
    full = value.strip()
    variants = [full]
    if ":" in full:
        suffix = full.rsplit(":", 1)[1].strip()
        if suffix and suffix != full:
            variants.append(suffix)
    return tuple(variants)


def _load_tree(path: Path) -> ET.ElementTree:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EafFilterError(f"Cannot read input file: {exc}") from exc

    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise EafFilterError("DTD and entity declarations are not supported.")

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(raw, parser=parser)
    except ET.ParseError as exc:
        raise EafFilterError(f"Malformed XML: {exc}") from exc
    return ET.ElementTree(root)


def _validate_and_index(
    tree: ET.ElementTree,
) -> tuple[ET.Element, ET.Element, dict[str, int], set[str]]:
    root = tree.getroot()
    if _local_name(root.tag) != "ANNOTATION_DOCUMENT":
        raise EafFilterError("The root element is not ANNOTATION_DOCUMENT.")

    time_orders = _children(root, "TIME_ORDER")
    if len(time_orders) != 1:
        raise EafFilterError("The EAF must contain exactly one TIME_ORDER element.")
    time_order = time_orders[0]

    tier_ids: set[str] = set()
    for tier in _children(root, "TIER"):
        tier_id = tier.get("TIER_ID", "")
        if not tier_id:
            raise EafFilterError("A TIER has no TIER_ID.")
        if tier_id in tier_ids:
            raise EafFilterError(f"Duplicate tier ID: {tier_id}")
        tier_ids.add(tier_id)

    time_values: dict[str, int] = {}
    for slot in _children(time_order, "TIME_SLOT"):
        slot_id = slot.get("TIME_SLOT_ID", "")
        if not slot_id:
            raise EafFilterError("A TIME_SLOT has no TIME_SLOT_ID.")
        if slot_id in time_values:
            raise EafFilterError(f"Duplicate time-slot ID: {slot_id}")
        raw_value = slot.get("TIME_VALUE")
        if raw_value is None:
            raise EafFilterError(f"Time slot {slot_id} has no TIME_VALUE.")
        try:
            time_values[slot_id] = int(raw_value)
        except ValueError as exc:
            raise EafFilterError(
                f"Time slot {slot_id} has a non-integer TIME_VALUE."
            ) from exc

    annotation_ids: set[str] = set()
    annotation_references: list[tuple[str, str]] = []
    initially_referenced_slots: set[str] = set()
    for annotation in _descendants(root, "ALIGNABLE_ANNOTATION"):
        annotation_id = annotation.get("ANNOTATION_ID", "")
        if not annotation_id:
            raise EafFilterError("An ALIGNABLE_ANNOTATION has no ANNOTATION_ID.")
        if annotation_id in annotation_ids:
            raise EafFilterError(f"Duplicate annotation ID: {annotation_id}")
        annotation_ids.add(annotation_id)

        start_ref = annotation.get("TIME_SLOT_REF1", "")
        end_ref = annotation.get("TIME_SLOT_REF2", "")
        if start_ref not in time_values or end_ref not in time_values:
            raise EafFilterError(
                f"Annotation {annotation_id} has a missing time-slot reference."
            )
        if time_values[start_ref] >= time_values[end_ref]:
            raise EafFilterError(
                f"Annotation {annotation_id} has a non-positive time interval."
            )
        initially_referenced_slots.update((start_ref, end_ref))
        _annotation_value(annotation)

    for annotation in _descendants(root, "REF_ANNOTATION"):
        annotation_id = annotation.get("ANNOTATION_ID", "")
        if not annotation_id:
            raise EafFilterError("A REF_ANNOTATION has no ANNOTATION_ID.")
        if annotation_id in annotation_ids:
            raise EafFilterError(f"Duplicate annotation ID: {annotation_id}")
        annotation_ids.add(annotation_id)
        target = annotation.get("ANNOTATION_REF", "")
        annotation_references.append((annotation_id, target))
        previous = annotation.get("PREVIOUS_ANNOTATION")
        if previous:
            annotation_references.append((annotation_id, previous))
        _annotation_value(annotation)

    for annotation_id, target in annotation_references:
        if target not in annotation_ids:
            raise EafFilterError(
                f"Reference annotation {annotation_id} points to missing annotation {target}."
            )

    return root, time_order, time_values, initially_referenced_slots


def _tier_info(tier: ET.Element, time_values: dict[str, int]) -> TierInfo:
    records: list[AnnotationRecord] = []
    wrappers = _children(tier, "ANNOTATION")
    only_alignable = True
    for wrapper in wrappers:
        annotations = [child for child in wrapper if _local_name(child.tag)]
        if len(annotations) != 1 or _local_name(annotations[0].tag) != "ALIGNABLE_ANNOTATION":
            only_alignable = False
            continue
        annotation = annotations[0]
        start_ref = annotation.get("TIME_SLOT_REF1", "")
        end_ref = annotation.get("TIME_SLOT_REF2", "")
        records.append(
            AnnotationRecord(
                wrapper=wrapper,
                annotation=annotation,
                interval=(time_values[start_ref], time_values[end_ref]),
                value=_annotation_value(annotation),
            )
        )
    return TierInfo(
        element=tier,
        tier_id=tier.get("TIER_ID", ""),
        records=tuple(records),
        contains_only_alignable_annotations=only_alignable,
    )


def _is_confidence_tier(tier: TierInfo, selected_labels: set[str]) -> bool:
    if not tier.records or not tier.contains_only_alignable_annotations:
        return False
    values_look_numeric = all(_looks_like_confidence(record.value) for record in tier.records)
    return values_look_numeric or tier.tier_id in selected_labels


def discover_groups(
    root: ET.Element, time_values: dict[str, int]
) -> tuple[list[tuple[TierInfo, list[TierInfo]]], set[str]]:
    """Discover main tiers and their consecutive, time-aligned score tiers."""

    tiers = [_tier_info(tier, time_values) for tier in _children(root, "TIER")]
    groups: list[tuple[TierInfo, list[TierInfo]]] = []
    score_tier_ids: set[str] = set()
    index = 0

    while index < len(tiers):
        main = tiers[index]
        if (
            not main.records
            or not main.contains_only_alignable_annotations
            or all(_looks_like_confidence(record.value) for record in main.records)
        ):
            index += 1
            continue

        main_intervals = {record.interval for record in main.records}
        selected_labels = {
            variant
            for record in main.records
            for variant in _label_variants(record.value)
        }
        candidates: list[TierInfo] = []
        cursor = index + 1
        while cursor < len(tiers):
            candidate = tiers[cursor]
            candidate_intervals = {record.interval for record in candidate.records}
            if (
                not candidate_intervals
                or not candidate_intervals.issubset(main_intervals)
                or not _is_confidence_tier(candidate, selected_labels)
            ):
                break
            candidates.append(candidate)
            cursor += 1

        if candidates and any(
            candidate.tier_id in selected_labels for candidate in candidates
        ):
            groups.append((main, candidates))
            score_tier_ids.update(candidate.tier_id for candidate in candidates)
            index = cursor
        else:
            index += 1

    return groups, score_tier_ids


def _select_score_tier(
    value: str, score_tiers: Sequence[TierInfo]
) -> tuple[TierInfo | None, str | None]:
    by_name = {tier.tier_id: tier for tier in score_tiers}
    matches = [by_name[variant] for variant in _label_variants(value) if variant in by_name]
    unique_matches = list(dict.fromkeys(tier.tier_id for tier in matches))
    if not unique_matches:
        return None, "no score tier matches the selected label"
    if len(unique_matches) > 1:
        return None, "the selected label matches multiple score tiers"
    return by_name[unique_matches[0]], None


def _filter_group(
    main: TierInfo,
    score_tiers: Sequence[TierInfo],
    score_tier_owners: dict[str, str],
    threshold: float,
    warnings: list[str],
) -> GroupResult:
    result = GroupResult(
        main_tier=main.tier_id,
        score_tiers=[tier.tier_id for tier in score_tiers],
    )
    scores_by_tier: dict[str, dict[tuple[int, int], list[AnnotationRecord]]] = {}
    for tier in score_tiers:
        by_interval: dict[tuple[int, int], list[AnnotationRecord]] = defaultdict(list)
        for record in tier.records:
            by_interval[record.interval].append(record)
        scores_by_tier[tier.tier_id] = by_interval

    for record in main.records:
        score_tier, error = _select_score_tier(record.value, score_tiers)
        if error is not None or score_tier is None:
            foreign_matches = [
                (variant, score_tier_owners[variant])
                for variant in _label_variants(record.value)
                if variant in score_tier_owners
                and score_tier_owners[variant] != main.tier_id
            ]
            if foreign_matches:
                tier_id, owner = foreign_matches[0]
                error = (
                    f"matching score tier {tier_id!r} belongs to main tier "
                    f"{owner!r} and was not reused"
                )
            result.kept += 1
            result.warnings += 1
            warnings.append(
                f"{main.tier_id} {record.interval[0]}-{record.interval[1]} ms: "
                f"{error}; kept."
            )
            continue

        score_records = scores_by_tier[score_tier.tier_id].get(record.interval, [])
        if len(score_records) != 1:
            result.kept += 1
            result.warnings += 1
            reason = "missing" if not score_records else "duplicated"
            warnings.append(
                f"{main.tier_id} {record.interval[0]}-{record.interval[1]} ms: "
                f"{reason} confidence in {score_tier.tier_id}; kept."
            )
            continue

        try:
            confidence = parse_confidence(score_records[0].value)
        except ValueError as exc:
            result.kept += 1
            result.warnings += 1
            warnings.append(
                f"{main.tier_id} {record.interval[0]}-{record.interval[1]} ms: "
                f"invalid confidence in {score_tier.tier_id} ({exc}); kept."
            )
            continue

        if confidence < threshold:
            main.element.remove(record.wrapper)
            result.filtered += 1
        else:
            result.kept += 1

    return result


def _remaining_annotation_ids(root: ET.Element) -> set[str]:
    return {
        annotation_id
        for name in ("ALIGNABLE_ANNOTATION", "REF_ANNOTATION")
        for annotation in _descendants(root, name)
        if (annotation_id := annotation.get("ANNOTATION_ID"))
    }


def _validate_remaining_references(root: ET.Element) -> None:
    annotation_ids = _remaining_annotation_ids(root)
    for annotation in _descendants(root, "REF_ANNOTATION"):
        annotation_id = annotation.get("ANNOTATION_ID", "")
        for attribute in ("ANNOTATION_REF", "PREVIOUS_ANNOTATION"):
            target = annotation.get(attribute)
            if target and target not in annotation_ids:
                raise EafFilterError(
                    f"Filtering would leave {annotation_id} pointing to removed "
                    f"annotation {target}."
                )


def _remove_newly_orphaned_time_slots(
    root: ET.Element,
    time_order: ET.Element,
    initially_referenced: set[str],
) -> int:
    remaining_references: set[str] = set()
    for annotation in _descendants(root, "ALIGNABLE_ANNOTATION"):
        remaining_references.update(
            (
                annotation.get("TIME_SLOT_REF1", ""),
                annotation.get("TIME_SLOT_REF2", ""),
            )
        )
    newly_orphaned = initially_referenced - remaining_references
    removed = 0
    for slot in list(_children(time_order, "TIME_SLOT")):
        if slot.get("TIME_SLOT_ID") in newly_orphaned:
            time_order.remove(slot)
            removed += 1
    return removed


def _strip_configured_value_prefixes(root: ET.Element) -> int:
    stripped_count = 0
    for value_element in _descendants(root, "ANNOTATION_VALUE"):
        value = value_element.text or ""
        for prefix in VALUE_PREFIXES_TO_STRIP:
            if value.startswith(prefix):
                value_element.text = value[len(prefix) :].lstrip()
                stripped_count += 1
                break
    return stripped_count


def _strip_value_prefix_from_numeric_display_tiers(root: ET.Element) -> int:
    stripped_count = 0
    for tier in _children(root, "TIER"):
        if tier.get("TIER_ID") not in PLAIN_NUMERIC_VALUE_TIER_IDS:
            continue
        for value_element in _descendants(tier, "ANNOTATION_VALUE"):
            value = value_element.text or ""
            if value.startswith("value:"):
                value_element.text = value[len("value:") :].lstrip()
                stripped_count += 1
    return stripped_count


def _next_annotation_id(root: ET.Element) -> Iterable[str]:
    existing_ids = _remaining_annotation_ids(root)
    highest_number = max(
        (
            int(match.group(1))
            for annotation_id in existing_ids
            if (match := re.fullmatch(r"a(\d+)", annotation_id))
        ),
        default=0,
    )
    candidate = highest_number + 1
    while True:
        annotation_id = f"a{candidate}"
        candidate += 1
        if annotation_id not in existing_ids:
            existing_ids.add(annotation_id)
            yield annotation_id


def _populate_shot_segment_tier(
    root: ET.Element,
    time_values: dict[str, int],
    warnings: list[str],
) -> int:
    tiers = _children(root, "TIER")
    source = next(
        (tier for tier in tiers if tier.get("TIER_ID") == SHOT_BOUNDARY_SOURCE_TIER_ID),
        None,
    )
    target = next(
        (tier for tier in tiers if tier.get("TIER_ID") == SHOT_SEGMENT_TIER_ID),
        None,
    )

    if target is not None and _children(target, "ANNOTATION"):
        return 0
    if source is None:
        if target is None:
            return 0
        warnings.append(
            f"Shot segment tier was not created because source tier "
            f"{SHOT_BOUNDARY_SOURCE_TIER_ID!r} is missing."
        )
        return 0

    source_info = _tier_info(source, time_values)
    if not source_info.records or not source_info.contains_only_alignable_annotations:
        warnings.append(
            f"Shot segment tier was not created because source tier "
            f"{SHOT_BOUNDARY_SOURCE_TIER_ID!r} has no usable aligned annotations."
        )
        return 0

    if target is None:
        target_attributes = {
            "TIER_ID": SHOT_SEGMENT_TIER_ID,
            "LINGUISTIC_TYPE_REF": source.get("LINGUISTIC_TYPE_REF", "default-lt"),
        }
        target = ET.Element("TIER", target_attributes)
        source_index = list(root).index(source)
        root.insert(source_index, target)

    unique_records: list[AnnotationRecord] = []
    seen_intervals: set[tuple[int, int]] = set()
    for record in source_info.records:
        if record.interval not in seen_intervals:
            seen_intervals.add(record.interval)
            unique_records.append(record)

    annotation_ids = _next_annotation_id(root)
    for index, source_record in enumerate(unique_records):
        wrapper = ET.SubElement(target, "ANNOTATION")
        annotation = ET.SubElement(
            wrapper,
            "ALIGNABLE_ANNOTATION",
            {
                "ANNOTATION_ID": next(annotation_ids),
                "TIME_SLOT_REF1": source_record.annotation.get("TIME_SLOT_REF1", ""),
                "TIME_SLOT_REF2": source_record.annotation.get("TIME_SLOT_REF2", ""),
            },
        )
        ET.SubElement(annotation, "ANNOTATION_VALUE").text = f"value:{index}"
    return len(unique_records)


def _merge_regular_transcript(
    root: ET.Element,
    time_values: dict[str, int],
    warnings: list[str],
) -> int:
    transcript_tier = next(
        (
            tier
            for tier in _children(root, "TIER")
            if tier.get("TIER_ID") == MERGED_TRANSCRIPT_TIER_ID
        ),
        None,
    )
    if transcript_tier is None:
        return 0

    transcript_info = _tier_info(transcript_tier, time_values)
    if not transcript_info.records:
        return 0
    if not transcript_info.contains_only_alignable_annotations:
        warnings.append(
            f"Tier {MERGED_TRANSCRIPT_TIER_ID!r} contains unsupported reference "
            "annotations and was not combined."
        )
        return 0

    ordered_records = sorted(
        transcript_info.records,
        key=lambda record: (record.interval[0], record.interval[1]),
    )
    chunks: list[str] = []
    for record in ordered_records:
        value = record.value.strip()
        if value.startswith("Transcript:"):
            value = value[len("Transcript:") :].lstrip()
        if value:
            chunks.append(value)

    clip_start = min(time_values.values())
    clip_end = max(time_values.values())
    if clip_start >= clip_end:
        raise EafFilterError("Cannot determine a positive full-clip transcript interval.")
    start_ref = next(
        slot_id for slot_id, time_value in time_values.items() if time_value == clip_start
    )
    end_ref = next(
        slot_id for slot_id, time_value in time_values.items() if time_value == clip_end
    )

    retained = ordered_records[0]
    for record in transcript_info.records:
        transcript_tier.remove(record.wrapper)
    transcript_tier.append(retained.wrapper)
    retained.annotation.set("TIME_SLOT_REF1", start_ref)
    retained.annotation.set("TIME_SLOT_REF2", end_ref)
    value_element = _children(retained.annotation, "ANNOTATION_VALUE")[0]
    value_element.text = " ".join(chunks)
    return len(ordered_records)


def _consolidate_ocr_windows(
    root: ET.Element,
    time_values: dict[str, int],
    warnings: list[str],
) -> tuple[int, int]:
    ocr_tier = next(
        (
            tier
            for tier in _children(root, "TIER")
            if tier.get("TIER_ID") == CONSOLIDATED_OCR_TIER_ID
        ),
        None,
    )
    if ocr_tier is None:
        return 0, 0

    ocr_info = _tier_info(ocr_tier, time_values)
    if not ocr_info.records:
        return 0, 0
    if not ocr_info.contains_only_alignable_annotations:
        warnings.append(
            f"Tier {CONSOLIDATED_OCR_TIER_ID!r} contains unsupported reference "
            "annotations and was not consolidated."
        )
        return len(ocr_info.records), len(ocr_info.records)

    records_by_interval: dict[tuple[int, int], list[AnnotationRecord]] = defaultdict(list)
    for record in ocr_info.records:
        records_by_interval[record.interval].append(record)

    retained_records: list[AnnotationRecord] = []
    for interval in sorted(records_by_interval):
        records = records_by_interval[interval]
        retained = records[0]
        combined_value = " ".join(
            record.value.strip() for record in records if record.value.strip()
        )
        _children(retained.annotation, "ANNOTATION_VALUE")[0].text = combined_value
        retained_records.append(retained)

    for record in ocr_info.records:
        ocr_tier.remove(record.wrapper)
    for record in retained_records:
        ocr_tier.append(record.wrapper)

    return len(ocr_info.records), len(retained_records)


def filter_tree(tree: ET.ElementTree, threshold: float) -> FilterResult:
    if not 0.0 <= threshold <= 1.0 or not math.isfinite(threshold):
        raise EafFilterError("The confidence threshold must be between 0 and 1.")

    root, time_order, time_values, initially_referenced = _validate_and_index(tree)
    groups, score_tier_ids = discover_groups(root, time_values)
    result = FilterResult(output_path=None)
    score_tier_owners = {
        score_tier.tier_id: main.tier_id
        for main, score_tiers in groups
        for score_tier in score_tiers
    }

    for main, score_tiers in groups:
        result.groups.append(
            _filter_group(
                main,
                score_tiers,
                score_tier_owners,
                threshold,
                result.warnings,
            )
        )

    for tier in list(_children(root, "TIER")):
        if tier.get("TIER_ID") in score_tier_ids:
            root.remove(tier)

    discovered_ids = {
        tier.tier_id for _main, score_tiers in groups for tier in score_tiers
    }
    for tier in _children(root, "TIER"):
        info = _tier_info(tier, time_values)
        if (
            info.tier_id not in discovered_ids
            and info.records
            and info.contains_only_alignable_annotations
            and all(_looks_like_confidence(record.value) for record in info.records)
        ):
            result.warnings.append(
                f"Numeric tier {info.tier_id!r} was not part of a verified group; preserved."
            )

    result.transcript_chunks_combined = _merge_regular_transcript(
        root, time_values, result.warnings
    )
    result.stripped_value_prefixes = _strip_configured_value_prefixes(root)
    (
        result.original_ocr_annotations,
        result.consolidated_ocr_windows,
    ) = _consolidate_ocr_windows(root, time_values, result.warnings)
    result.created_shot_segments = _populate_shot_segment_tier(
        root, time_values, result.warnings
    )
    result.stripped_value_prefixes += _strip_value_prefix_from_numeric_display_tiers(
        root
    )
    _validate_remaining_references(root)
    result.removed_time_slots = _remove_newly_orphaned_time_slots(
        root, time_order, initially_referenced
    )
    return result


def _serialize_tree(tree: ET.ElementTree) -> bytes:
    ET.register_namespace("xsi", _XSI_NAMESPACE)
    ET.indent(tree, space="\t")
    return ET.tostring(tree.getroot(), encoding="utf-8", short_empty_elements=True) + b"\n"


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise EafFilterError(f"Output already exists: {path}") from exc
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise EafFilterError(f"Cannot write output file: {exc}") from exc


def _write_replacing(path: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise EafFilterError(f"Cannot replace output file: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def filter_eaf(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    threshold: float = CONFIDENCE_THRESHOLD,
    overwrite: bool = False,
) -> FilterResult:
    source = Path(input_path)
    if source.suffix.lower() != ".eaf":
        raise EafFilterError("Input file must have an .eaf extension.")
    destination = (
        Path(output_path)
        if output_path is not None
        else source.with_name(f"{source.stem}_filtered{source.suffix}")
    )
    try:
        same_path = source.resolve() == destination.resolve()
    except OSError:
        same_path = source.absolute() == destination.absolute()
    if same_path:
        raise EafFilterError("The output path cannot be the input file.")
    if destination.exists() and not overwrite:
        raise EafFilterError(f"Output already exists: {destination}")

    tree = _load_tree(source)
    result = filter_tree(tree, threshold)
    content = _serialize_tree(tree)
    if overwrite:
        _write_replacing(destination, content)
    else:
        _write_exclusive(destination, content)
    result.output_path = destination
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover confidence-backed EAF tier groups, remove low-confidence "
            "main annotations, and omit their score tiers."
        )
    )
    parser.add_argument("input", type=Path, help="Path to one .eaf file")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing _filtered.eaf output atomically",
    )
    return parser


def _print_result(result: FilterResult, threshold: float) -> None:
    print(f"Confidence threshold: {threshold:g}")
    if result.groups:
        for group in result.groups:
            scores = ", ".join(group.score_tiers)
            print(
                f"{group.main_tier}: kept={group.kept}, filtered={group.filtered}, "
                f"warnings={group.warnings}; removed score tiers: {scores}"
            )
    else:
        print("No verified confidence groups were discovered.")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    print(f"Removed time slots: {result.removed_time_slots}")
    print(f"Stripped value prefixes: {result.stripped_value_prefixes}")
    print(f"Created shot segments: {result.created_shot_segments}")
    print(f"Transcript chunks combined: {result.transcript_chunks_combined}")
    if result.original_ocr_annotations:
        print(
            f"OCR annotations consolidated: {result.original_ocr_annotations} -> "
            f"{result.consolidated_ocr_windows} windows"
        )
    print(f"Output: {result.output_path}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = filter_eaf(
            args.input,
            threshold=CONFIDENCE_THRESHOLD,
            overwrite=args.overwrite,
        )
    except EafFilterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_result(result, CONFIDENCE_THRESHOLD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
