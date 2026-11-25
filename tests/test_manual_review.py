from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mutagen.flac import FLAC

from core.flac_input import inspect_release
from core.rules_engine import (
    Edition, EditionDetails, EditionTrack, LocalFile, ManualField, MetadataPolicy, ReleaseContext, propose_metadata,
)
from core.tag_writer import InvalidApproval, apply_metadata
from tests.test_read_only_workflow import write_track
from ui.diff_table import ReviewApproval


class ManualReviewTests(unittest.TestCase):
    def test_corrected_vinyl_position_approves_side_and_number_together(self):
        edition = Edition("discogs", "10", "Vinyl", (EditionTrack("a", "Unclear"),), True)
        local = LocalFile("track", {"%VINYLSIDE%": ("A",), "%VINYLTRACKNUMBER%": ("1",)})
        context = ReleaseContext((local,), frozenset({local.file_id}), edition, {local.file_id: "a"},
                                 manual_vinyl_positions={local.file_id: "b2"})
        proposal = propose_metadata(context, MetadataPolicy())

        approval = ReviewApproval(proposal)
        approval.toggle((local.file_id, "%VINYLSIDE%"))

        self.assertEqual([(d.field, d.proposed_values, d.evidence.provider) for d in approval.selected()],
                         [("%VINYLSIDE%", ("B",), "manual"), ("%VINYLTRACKNUMBER%", ("2",), "manual")])

    def test_manual_changes_use_validated_values_and_keep_unmapped_files_unresolved(self):
        edition = Edition("discogs", "10", "WEB", (EditionTrack("a", "1", "Original title"),), True,
                          details=EditionDetails())
        first, second = LocalFile("first", {}), LocalFile("second", {"%BPM%": ("123",)})
        context = ReleaseContext((first, second), frozenset({first.file_id, second.file_id}), edition, {first.file_id: "a"},
                                 manual_fields={
                                     (first.file_id, "%TITLE%"): ManualField("set", ("Corrected title",)),
                                     (first.file_id, "%BPM%"): ManualField("set", ("0128.50",)),
                                     (first.file_id, "%MEDIATYPE%"): ManualField("set", ("CD",)),
                                     (second.file_id, "%BPM%"): ManualField("set", ("129",)),
                                 })
        proposal = propose_metadata(context, MetadataPolicy())

        selected = {(d.file_id, d.field): (d.state, d.proposed_values) for d in proposal.decisions}
        self.assertEqual(selected[first.file_id, "%TITLE%"], ("SET", ("Corrected title",)))
        self.assertEqual(selected[first.file_id, "%BPM%"], ("SET", ("128.5",)))
        self.assertEqual(selected[first.file_id, "%MEDIATYPE%"], ("UNRESOLVED", None))
        self.assertEqual(selected[second.file_id, "%BPM%"], ("UNRESOLVED", None))

    def test_manual_genre_removal_is_linked_and_requires_consistent_unselected_siblings(self):
        edition = Edition("discogs", "10", "", (EditionTrack("a", "1"), EditionTrack("b", "2")), True)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "a.flac", root / "b.flac"
            for path in (first, second):
                write_track(path, "Trance")

            partial = inspect_release([first], root)
            proposal = propose_metadata(ReleaseContext(partial.files, partial.selected_file_ids, edition, {first.name: "a"},
                                                      membership_complete=True, genre_action="delete"), MetadataPolicy())
            self.assertEqual([d.state for d in proposal.decisions if d.field == "%GENRE%"], ["UNRESOLVED"])

            complete = inspect_release([root])
            proposal = propose_metadata(ReleaseContext(complete.files, complete.selected_file_ids, edition,
                                                      {first.name: "a", second.name: "b"}, membership_complete=True,
                                                      genre_action="delete"), MetadataPolicy())
            approval = ReviewApproval(proposal)
            approval.toggle((first.name, "%GENRE%"))
            with self.assertRaises(InvalidApproval):
                apply_metadata(complete, proposal, approval.selected()[:1])
            result = apply_metadata(complete, proposal, approval.selected())

            self.assertEqual({item.file_id for item in result.fields if item.status == "changed"}, {first.name, second.name})
            for path in (first, second):
                self.assertNotIn("GENRE", FLAC(str(path)))
