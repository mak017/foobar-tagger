from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC

from core.flac_input import inspect_release
from core.matching import suggest_track_mappings
from core.rules_engine import Edition, LocalFile, MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import InvalidApproval, apply_metadata
from providers.discogs import load_release
from tests.test_read_only_workflow import write_track
from ui.diff_table import ReviewApproval


NUMBERING_FIELDS = ("%TRACKNUMBER%", "%TOTALTRACKS%", "%DISCNUMBER%", "%TOTALDISCS%")


def edition_fixture(formats: list[dict[str, str]], positions: tuple[str, ...]) -> Edition:
    payload = {"id": 123, "title": "Afterlight", "formats": formats, "tracklist": [
        {"type_": "track", "title": f"Track {index}", "position": position}
        for index, position in enumerate(positions, 1)
    ]}
    with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode())):
        return load_release("https://www.discogs.com/release/123").edition


class DiscAndWebNumberingTests(unittest.TestCase):
    def test_single_selection_gets_per_disc_numbers_from_two_disc_edition(self):
        edition = edition_fixture([{"name": "CD", "qty": "2"}], ("CD1-01", "CD1-02", "CD2-01", "CD2-02", "CD2-03"))
        local = LocalFile("selected", {"%TRACKNUMBER%": ("4",), "%TOTALTRACKS%": ("5",)})
        context = ReleaseContext((local, LocalFile("sibling", {})), frozenset({local.file_id}), edition,
                                 {local.file_id: edition.tracks[3].track_id})

        proposal = propose_metadata(context, MetadataPolicy())

        self.assertEqual({decision.file_id for decision in proposal.decisions}, {local.file_id})
        expected = {"%TRACKNUMBER%": ("2",), "%TOTALTRACKS%": ("3",), "%DISCNUMBER%": ("2",), "%TOTALDISCS%": ("2",)}
        decisions = {decision.field: decision for decision in proposal.decisions if decision.field in expected}
        self.assertEqual({field: decision.proposed_values for field, decision in decisions.items()}, expected)
        self.assertEqual({decision.evidence.original_value for decision in decisions.values()}, {"CD2-02"})

    def test_dvd_box_set_uses_all_matching_carriers(self):
        edition = edition_fixture(
            [{"name": "All Media", "qty": "1"}, {"name": "DVD", "qty": "1"}, {"name": "DVD", "qty": "2"}],
            ("DVD1-1", "DVD2-1", "DVD3-1", "DVD3-2"),
        )
        local = LocalFile("DVD 3/Track 4.flac", {"%DISCNUMBER%": ("3",), "%TRACKNUMBER%": ("2",)})
        context = ReleaseContext((local,), frozenset({local.file_id}), edition, {})
        (suggestion,) = suggest_track_mappings(context)
        self.assertEqual(suggestion.suggested_id, edition.tracks[3].track_id)
        context = replace(context, track_mapping={local.file_id: edition.tracks[3].track_id})

        proposal = propose_metadata(context, MetadataPolicy())

        self.assertEqual(edition.disc_count, 3)
        self.assertEqual({decision.field: decision.proposed_values for decision in proposal.decisions
                          if decision.field in NUMBERING_FIELDS}, dict(zip(NUMBERING_FIELDS, (("2",), ("2",), ("3",), ("3",)))))

    def test_single_cd_and_web_remove_disc_tags_and_count_the_full_edition(self):
        for media, positions in (("CD", ("01", "02", "03")), ("File", ("1-1", "", "2-1"))):
            with self.subTest(media=media):
                edition = edition_fixture([{"name": media, "qty": "1"}], positions)
                local = LocalFile("selected.flac", {"%DISCNUMBER%": ("2",), "%TOTALDISCS%": ("2",)})
                context = ReleaseContext((local,), frozenset({local.file_id}), edition,
                                         {local.file_id: edition.tracks[1].track_id})

                proposal = propose_metadata(context, MetadataPolicy())

                self.assertEqual({decision.field: (decision.state, decision.proposed_values) for decision in proposal.decisions
                                  if decision.field in NUMBERING_FIELDS}, dict(zip(NUMBERING_FIELDS, (
                    ("SET", ("2",)), ("SET", ("3",)), ("DELETE", None), ("DELETE", None),
                ))))

    def test_uncertain_cd_layout_keeps_existing_numbering_available_in_review(self):
        for positions, quantity in (
            (("1", "2"), "2"), (("1-1", "2-1"), "3"), (("1-1", "2-1", "2-1"), "2"),
            (("1-1", "1-3"), "1"), (("1.1", "2.1"), "2"), (("1-1", ""), "1"),
        ):
            with self.subTest(positions=positions, quantity=quantity):
                edition = edition_fixture([{"name": "CD", "qty": quantity}], positions)
                local = LocalFile("selected.flac", dict.fromkeys(NUMBERING_FIELDS, ("7",)))
                context = ReleaseContext((local,), frozenset({local.file_id}), edition,
                                         {local.file_id: edition.tracks[0].track_id})

                proposal = propose_metadata(context, MetadataPolicy())

                self.assertEqual({decision.field: (decision.state, decision.existing_values) for decision in proposal.decisions
                                  if decision.field in NUMBERING_FIELDS}, dict.fromkeys(NUMBERING_FIELDS, ("UNRESOLVED", ("7",))))
                self.assertIn("DISC_LAYOUT_UNRESOLVED", {issue.code for issue in proposal.issues})
                self.assertTrue(any(decision.field == "%TITLE%" and decision.state == "SET" for decision in proposal.decisions))

    def test_incomplete_web_keeps_track_numbers_and_only_mapped_file_can_remove_disc_tags(self):
        edition = replace(edition_fixture([{"name": "File"}], ("1", "2")), tracklist_complete=False)
        files = tuple(LocalFile(name, dict.fromkeys(NUMBERING_FIELDS, ("2",))) for name in ("mapped", "unmapped"))
        context = ReleaseContext(files, frozenset({"mapped", "unmapped"}), edition, {"mapped": edition.tracks[0].track_id})

        proposal = propose_metadata(context, MetadataPolicy())

        expected = {(file.file_id, field): "UNRESOLVED" for file in files for field in NUMBERING_FIELDS}
        expected.update({("mapped", field): "DELETE" for field in NUMBERING_FIELDS[2:]})
        self.assertEqual({(decision.file_id, decision.field): decision.state for decision in proposal.decisions
                          if decision.field in NUMBERING_FIELDS}, expected)

    def test_approves_disc_pairs_per_file_and_writes_only_selected_flacs(self):
        edition = edition_fixture([{"name": "CD", "qty": "2"}], ("1-1", "2-1", "2-2"))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            first, second, sibling = (root / name for name in ("A.flac", "B.flac", "C.flac"))
            for path in (first, second, sibling):
                write_track(path, "Trance")
                audio = FLAC(str(path))
                audio["TRACKNUMBER"] = "9"
                audio["TRACKTOTAL"] = "9"
                audio["DISCNUMBER"] = "9"
                audio["DISCTOTAL"] = "9"
                audio.save()
            original = sibling.read_bytes()
            local = inspect_release([first, second], release_folder=root)
            context = ReleaseContext(local.files, local.selected_file_ids, edition,
                                     {first.name: edition.tracks[0].track_id, second.name: edition.tracks[2].track_id})
            proposal = propose_metadata(context, MetadataPolicy())
            approval = ReviewApproval(proposal)

            approval.toggle((first.name, "%DISCNUMBER%"))
            self.assertEqual({(decision.file_id, decision.field) for decision in approval.selected()},
                             {(first.name, "%DISCNUMBER%"), (first.name, "%TOTALDISCS%")})
            with self.assertRaises(InvalidApproval):
                apply_metadata(local, proposal, approval.selected()[:1])

            approval.toggle((second.name, "%TOTALDISCS%"))
            for path in (first, second):
                approval.toggle((path.name, "%TRACKNUMBER%"))
                approval.toggle((path.name, "%TOTALTRACKS%"))
            result = apply_metadata(local, proposal, approval.selected())

            self.assertEqual({(item.file_id, item.field) for item in result.fields if item.status == "changed"},
                             {(path.name, field) for path in (first, second) for field in NUMBERING_FIELDS})
            for path, number in ((first, "1"), (second, "2")):
                written = FLAC(str(path))
                self.assertEqual([written[key] for key in ("TRACKNUMBER", "TOTALTRACKS", "DISCNUMBER", "TOTALDISCS")],
                                 [[number], [number], [number], ["2"]])
                self.assertNotIn("TRACKTOTAL", written)
                self.assertNotIn("DISCTOTAL", written)
            self.assertEqual(sibling.read_bytes(), original)
