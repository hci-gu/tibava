from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import filter_eaf_confidence as filterer


class EafFixture:
    def __init__(self) -> None:
        self.root = ET.Element("ANNOTATION_DOCUMENT", VERSION="2.8", FORMAT="2.8")
        ET.SubElement(self.root, "HEADER")
        self.time_order = ET.SubElement(self.root, "TIME_ORDER")
        self.next_slot = 1
        self.next_annotation = 1

    def add_tier(
        self, name: str, annotations: list[tuple[int, int, str]]
    ) -> "EafFixture":
        tier = ET.SubElement(self.root, "TIER", TIER_ID=name, LINGUISTIC_TYPE_REF="default-lt")
        for start, end, value in annotations:
            start_id = f"ts{self.next_slot}"
            end_id = f"ts{self.next_slot + 1}"
            self.next_slot += 2
            ET.SubElement(
                self.time_order,
                "TIME_SLOT",
                TIME_SLOT_ID=start_id,
                TIME_VALUE=str(start),
            )
            ET.SubElement(
                self.time_order,
                "TIME_SLOT",
                TIME_SLOT_ID=end_id,
                TIME_VALUE=str(end),
            )
            wrapper = ET.SubElement(tier, "ANNOTATION")
            annotation = ET.SubElement(
                wrapper,
                "ALIGNABLE_ANNOTATION",
                ANNOTATION_ID=f"a{self.next_annotation}",
                TIME_SLOT_REF1=start_id,
                TIME_SLOT_REF2=end_id,
            )
            self.next_annotation += 1
            ET.SubElement(annotation, "ANNOTATION_VALUE").text = value
        return self

    def tree(self) -> ET.ElementTree:
        ET.SubElement(
            self.root,
            "LINGUISTIC_TYPE",
            LINGUISTIC_TYPE_ID="default-lt",
            TIME_ALIGNABLE="true",
        )
        return ET.ElementTree(self.root)


def tier_values(tree: ET.ElementTree, name: str) -> list[str]:
    for tier in tree.getroot().findall("TIER"):
        if tier.get("TIER_ID") == name:
            return [
                value.text or ""
                for value in tier.findall("./ANNOTATION/ALIGNABLE_ANNOTATION/ANNOTATION_VALUE")
            ]
    raise AssertionError(f"Tier {name!r} not found")


class ConfidenceParsingTests(unittest.TestCase):
    def test_decimal_and_percentage_values(self) -> None:
        self.assertEqual(filterer.parse_confidence("value:0.731"), 0.731)
        self.assertEqual(filterer.parse_confidence("70.5%"), 0.705)
        self.assertEqual(filterer.parse_confidence("0.5"), 0.5)

    def test_invalid_values(self) -> None:
        for value in ("value:nope", "101%", "-0.1", "1.1", "nan"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                filterer.parse_confidence(value)


class FilteringTests(unittest.TestCase):
    def test_empty_shots_tier_is_populated_from_shot_boundaries(self) -> None:
        tree = (
            EafFixture()
            .add_tier("Shots", [])
            .add_tier("Shot", [(0, 100, "Speech"), (100, 200, "Music")])
            .tree()
        )

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual(tier_values(tree, "Shots"), ["0", "1"])
        self.assertEqual(tier_values(tree, "Shot"), ["Speech", "Music"])
        self.assertEqual(result.created_shot_segments, 2)
        shots = tree.getroot().find("./TIER[@TIER_ID='Shots']")
        shot = tree.getroot().find("./TIER[@TIER_ID='Shot']")
        self.assertIsNotNone(shots)
        self.assertIsNotNone(shot)
        self.assertEqual(
            [
                (node.get("TIME_SLOT_REF1"), node.get("TIME_SLOT_REF2"))
                for node in shots.findall("./ANNOTATION/ALIGNABLE_ANNOTATION")
            ],
            [
                (node.get("TIME_SLOT_REF1"), node.get("TIME_SLOT_REF2"))
                for node in shot.findall("./ANNOTATION/ALIGNABLE_ANNOTATION")
            ],
        )

    def test_audio_rms_value_prefix_is_stripped_without_affecting_other_scalars(self) -> None:
        tree = (
            EafFixture()
            .add_tier("Audio RMS", [(0, 100, "value:0.25")])
            .add_tier("Other Scalar", [(0, 100, "value:0.75")])
            .tree()
        )

        filterer.filter_tree(tree, 0.5)

        self.assertEqual(tier_values(tree, "Audio RMS"), ["0.25"])
        self.assertEqual(tier_values(tree, "Other Scalar"), ["value:0.75"])

    def test_requested_value_prefixes_are_stripped(self) -> None:
        values = [
            "Transcript:spoken text",
            "Audio Gender:Male",
            "Emotion:Happy",
            "Shot Size:Close-Up",
            "Shot Scale:Full",
            "Shot Movement:Static",
            "Sentiment:positive",
            "unconfigured:preserved",
        ]
        tree = EafFixture().add_tier(
            "Labels",
            [(index * 100, (index + 1) * 100, value) for index, value in enumerate(values)],
        ).tree()

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual(
            tier_values(tree, "Labels"),
            [
                "spoken text",
                "Male",
                "Happy",
                "Close-Up",
                "Full",
                "Static",
                "positive",
                "unconfigured:preserved",
            ],
        )
        self.assertEqual(result.stripped_value_prefixes, 7)

    def test_generic_discovery_and_per_interval_filtering(self) -> None:
        tree = (
            EafFixture()
            .add_tier("Energy", [(0, 100, "value:0.1"), (100, 200, "value:0.9")])
            .add_tier("Arbitrary Main", [(0, 100, "Kind:Alpha"), (100, 200, "Kind:Alpha")])
            .add_tier("Alpha", [(0, 100, "value:0.4"), (100, 200, "value:0.8")])
            .add_tier("Beta", [(0, 100, "value:0.9"), (100, 200, "value:0.1")])
            .add_tier("Never Selected", [(0, 100, "value:0.2"), (100, 200, "value:0.2")])
            .add_tier("Following Labels", [(0, 100, "plain"), (100, 200, "plain")])
            .tree()
        )

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual([group.main_tier for group in result.groups], ["Arbitrary Main"])
        self.assertEqual(tier_values(tree, "Arbitrary Main"), ["Kind:Alpha"])
        self.assertEqual(tier_values(tree, "Energy"), ["value:0.1", "value:0.9"])
        remaining_tiers = [tier.get("TIER_ID") for tier in tree.getroot().findall("TIER")]
        self.assertNotIn("Alpha", remaining_tiers)
        self.assertNotIn("Beta", remaining_tiers)
        self.assertNotIn("Never Selected", remaining_tiers)
        self.assertTrue(any("Energy" in warning for warning in result.warnings))
        self.assertEqual(result.removed_time_slots, 14)

    def test_threshold_is_inclusive_and_largest_score_is_ignored(self) -> None:
        tree = (
            EafFixture()
            .add_tier("Decision", [(0, 100, "Selected")])
            .add_tier("Selected", [(0, 100, "value:0.5")])
            .add_tier("Larger", [(0, 100, "value:0.99")])
            .tree()
        )

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual(tier_values(tree, "Decision"), ["Selected"])
        self.assertEqual(result.groups[0].kept, 1)
        self.assertEqual(result.groups[0].filtered, 0)

    def test_percentage_group(self) -> None:
        tree = (
            EafFixture()
            .add_tier("Unknown Group", [(0, 100, "Class:Yes"), (100, 200, "Class:No")])
            .add_tier("Yes", [(0, 100, "49.9%"), (100, 200, "10%")])
            .add_tier("No", [(0, 100, "50.1%"), (100, 200, "75%")])
            .tree()
        )

        filterer.filter_tree(tree, 0.5)

        self.assertEqual(tier_values(tree, "Unknown Group"), ["Class:No"])

    def test_missing_duplicate_malformed_and_out_of_range_scores_are_kept(self) -> None:
        fixture = EafFixture().add_tier(
            "Main",
            [
                (0, 100, "Type:A"),
                (100, 200, "Type:B"),
                (200, 300, "Type:C"),
                (300, 400, "Type:D"),
            ],
        )
        fixture.add_tier("A", [(100, 200, "value:0.2")])
        fixture.add_tier("B", [(100, 200, "value:0.2"), (100, 200, "value:0.3")])
        fixture.add_tier("C", [(200, 300, "value:not-a-number")])
        fixture.add_tier("D", [(300, 400, "value:1.2")])
        tree = fixture.add_tier("End", [(0, 400, "label")]).tree()

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual(len(tier_values(tree, "Main")), 4)
        self.assertEqual(result.groups[0].warnings, 4)
        self.assertEqual(len(result.warnings), 4)

    def test_score_tier_from_another_group_is_not_reused(self) -> None:
        tree = (
            EafFixture()
            .add_tier("First Main", [(0, 100, "Type:Shared"), (100, 200, "Type:Shared")])
            .add_tier("Shared", [(0, 100, "value:0.9"), (100, 200, "value:0.9")])
            .add_tier("Second Main", [(0, 100, "Type:Shared"), (100, 200, "Type:Local")])
            .add_tier("Local", [(0, 100, "value:0.1"), (100, 200, "value:0.9")])
            .tree()
        )

        result = filterer.filter_tree(tree, 0.5)

        self.assertEqual(tier_values(tree, "Second Main"), ["Type:Shared", "Type:Local"])
        warning = next(
            warning for warning in result.warnings if warning.startswith("Second Main")
        )
        self.assertIn("belongs to main tier 'First Main'", warning)


class FileSafetyTests(unittest.TestCase):
    def test_malformed_xml_and_broken_reference_do_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            malformed = folder / "malformed.eaf"
            malformed.write_text("<ANNOTATION_DOCUMENT>", encoding="utf-8")
            with self.assertRaises(filterer.EafFilterError):
                filterer.filter_eaf(malformed)
            self.assertFalse((folder / "malformed_filtered.eaf").exists())

            broken = folder / "broken.eaf"
            broken.write_text(
                "<ANNOTATION_DOCUMENT><TIME_ORDER/>"
                "<TIER TIER_ID='x'><ANNOTATION><ALIGNABLE_ANNOTATION "
                "ANNOTATION_ID='a1' TIME_SLOT_REF1='missing' TIME_SLOT_REF2='also_missing'>"
                "<ANNOTATION_VALUE>x</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION>"
                "</ANNOTATION></TIER></ANNOTATION_DOCUMENT>",
                encoding="utf-8",
            )
            with self.assertRaises(filterer.EafFilterError):
                filterer.filter_eaf(broken)
            self.assertFalse((folder / "broken_filtered.eaf").exists())

    def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.eaf"
            EafFixture().add_tier("Labels", [(0, 100, "x")]).tree().write(
                source, encoding="utf-8"
            )
            destination = folder / "input_filtered.eaf"
            destination.write_text("sentinel", encoding="utf-8")

            with self.assertRaises(filterer.EafFilterError):
                filterer.filter_eaf(source)

            self.assertEqual(destination.read_text(encoding="utf-8"), "sentinel")

    def test_explicit_overwrite_replaces_existing_filtered_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.eaf"
            EafFixture().add_tier(
                "Labels", [(0, 100, "Transcript:clean me")]
            ).tree().write(source, encoding="utf-8")
            destination = folder / "input_filtered.eaf"
            destination.write_text("sentinel", encoding="utf-8")

            result = filterer.filter_eaf(source, overwrite=True)

            self.assertEqual(result.output_path, destination)
            output_tree = ET.parse(destination)
            self.assertEqual(tier_values(output_tree, "Labels"), ["clean me"])


if __name__ == "__main__":
    unittest.main()
