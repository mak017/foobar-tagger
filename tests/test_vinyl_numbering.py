import unittest
from dataclasses import replace

from core.rules_engine import (
    Edition, EditionTrack, InvalidProposalInput, LocalFile, MetadataPolicy, ReleaseContext,
    propose_metadata,
)


class VinylNumberingTests(unittest.TestCase):
    def test_invalid_identity_or_mapping_is_rejected_before_proposing_changes(self):
        context = ReleaseContext(
            files=(LocalFile("file", {}),), selected_file_ids=frozenset(("file",)),
            edition=Edition("discogs", "123", "Vinyl", (EditionTrack("track", "A1"),), True),
            track_mapping={"file": "track"},
        )
        cases = (
            (replace(context, files=context.files * 2), "Duplicate file ID"),
            (replace(context, edition=replace(context.edition, tracks=context.edition.tracks * 2)),
             "Duplicate track ID"),
            (replace(context, selected_file_ids=frozenset(("missing",))), "Unknown selected file"),
            (replace(context, track_mapping={"missing": "track"}), "Unknown mapped file"),
            (replace(context, track_mapping={"file": "missing"}), "Unknown mapped track"),
        )
        for invalid, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(InvalidProposalInput, message):
                    propose_metadata(invalid, MetadataPolicy())

    def test_unrecognized_position_requires_review_without_inventing_side(self):
        context = ReleaseContext(
            files=(LocalFile("file", {"%VINYLSIDE%": ("Z",), "%DISCNUMBER%": ("1",)}),),
            selected_file_ids=frozenset(("file",)),
            edition=Edition("discogs", "unknown-position", "Vinyl", (
                EditionTrack("track", "1-1"),
            ), tracklist_complete=True),
            track_mapping={"file": "track"},
        )
        result = propose_metadata(context, MetadataPolicy())
        decisions = {d.field: d for d in result.decisions}
        self.assertEqual(decisions["%VINYLSIDE%"].state, "UNRESOLVED")
        self.assertEqual(decisions["%VINYLSIDE%"].existing_values, ("Z",))
        self.assertIsNone(decisions["%VINYLSIDE%"].proposed_values)
        self.assertEqual(decisions["%VINYLTRACKNUMBER%"].state, "UNRESOLVED")
        self.assertEqual(decisions["%TRACKNUMBER%"].proposed_values, ("1",))
        self.assertEqual(decisions["%DISCNUMBER%"].state, "DELETE")
        self.assertEqual(result.issues[0].code, "VINYL_POSITION_REQUIRED")

    def test_incomplete_tracklist_does_not_invent_release_numbers(self):
        context = ReleaseContext(
            files=(LocalFile("file", {"%TRACKNUMBER%": ("8",), "%DISCNUMBER%": ("2",)}),),
            selected_file_ids=frozenset(("file",)),
            edition=Edition("discogs", "incomplete", "Vinyl", (
                EditionTrack("track", "B2"),
            ), tracklist_complete=False),
            track_mapping={"file": "track"},
        )
        result = propose_metadata(context, MetadataPolicy())
        decisions = {d.field: d for d in result.decisions}
        self.assertEqual(decisions["%TRACKNUMBER%"].state, "UNRESOLVED")
        self.assertEqual(decisions["%TOTALTRACKS%"].state, "UNRESOLVED")
        self.assertEqual(decisions["%VINYLSIDE%"].proposed_values, ("B",))
        self.assertEqual(decisions["%DISCNUMBER%"].state, "DELETE")
        self.assertEqual(result.issues[0].code, "INCOMPLETE_TRACKLIST")

    def test_unmapped_track_is_unresolved_and_preserves_existing_tags(self):
        context = ReleaseContext(
            files=(LocalFile("unknown", {"%TRACKNUMBER%": ("7",)}),),
            selected_file_ids=frozenset(("unknown",)),
            edition=Edition("discogs", "123", "Vinyl", (
                EditionTrack("a", "A1"),
            ), tracklist_complete=True),
            track_mapping={},
        )
        result = propose_metadata(context, MetadataPolicy())
        self.assertEqual(len(result.decisions), 6)
        self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
        decision = next(d for d in result.decisions if d.field == "%TRACKNUMBER%")
        self.assertEqual(decision.existing_values, ("7",))
        self.assertIsNone(decision.proposed_values)
        self.assertEqual(result.issues[0].code, "MAPPING_REQUIRED")
        self.assertEqual(result.issues[0].file_ids, ("unknown",))
        self.assertIn("manual_mapping", result.issues[0].options)

    def test_partial_selection_uses_full_release_and_keeps_correct_values(self):
        tags = {
            "%TRACKNUMBER%": ("3",), "%TOTALTRACKS%": ("4",),
            "%VINYLSIDE%": ("B",), "%VINYLTRACKNUMBER%": ("1",),
        }
        context = ReleaseContext(
            files=(LocalFile("unselected", {}), LocalFile("selected", tags)),
            selected_file_ids=frozenset(("selected",)),
            edition=Edition("discogs", "partial", "Vinyl", (
                EditionTrack("a1", "A1"), EditionTrack("a2", "A2"),
                EditionTrack("b1", "B1"), EditionTrack("b2", "B2"),
            ), tracklist_complete=True),
            track_mapping={"selected": "b1"},
        )
        result = propose_metadata(context, MetadataPolicy())
        self.assertEqual({d.file_id for d in result.decisions}, {"selected"})
        self.assertTrue(all(d.state == "KEEP" for d in result.decisions))
        self.assertEqual({d.field: d.proposed_values for d in result.decisions}, {
            **tags, "%DISCNUMBER%": None, "%TOTALDISCS%": None,
        })
        self.assertEqual(result, propose_metadata(context, MetadataPolicy()))

    def test_side_only_positions_remove_stale_within_side_numbers(self):
        context = ReleaseContext(
            files=(LocalFile("a", {"%VINYLTRACKNUMBER%": ("9",)}), LocalFile("b", {})),
            selected_file_ids=frozenset(("a", "b")),
            edition=Edition("discogs", "side-only", "LP", (
                EditionTrack("ta", "A"), EditionTrack("tb", "B"),
            ), tracklist_complete=True),
            track_mapping={"a": "ta", "b": "tb"},
        )
        result = propose_metadata(context, MetadataPolicy())
        actual = {(d.file_id, d.field): (d.state, d.proposed_values) for d in result.decisions}
        self.assertEqual(actual[("a", "%VINYLSIDE%")], ("SET", ("A",)))
        self.assertEqual(actual[("b", "%TRACKNUMBER%")], ("SET", ("2",)))
        self.assertEqual(actual[("a", "%VINYLTRACKNUMBER%")], ("DELETE", None))
        self.assertEqual(actual[("b", "%VINYLTRACKNUMBER%")], ("KEEP", None))
        self.assertEqual(actual[("a", "%DISCNUMBER%")], ("KEEP", None))

    def test_multi_record_vinyl_uses_release_order_and_removes_disc_tags(self):
        positions = ("A1", "A2", "B1", "C1", "D1")
        context = ReleaseContext(
            files=tuple(LocalFile(str(i), {
                "%DISCNUMBER%": ("1",), "%TOTALDISCS%": ("2",),
                "%COMMENT%": ("Keep this",),
            }) for i in range(5)),
            selected_file_ids=frozenset(str(i) for i in range(5)),
            edition=Edition("discogs", "123", "Vinyl", tuple(
                EditionTrack(str(i), position) for i, position in enumerate(positions)
            ), tracklist_complete=True),
            track_mapping={str(i): str(i) for i in range(5)},
        )

        result = propose_metadata(context, MetadataPolicy())

        expected = (
            ("0", "1", "A", "1"), ("1", "2", "A", "2"),
            ("2", "3", "B", "1"), ("3", "4", "C", "1"),
            ("4", "5", "D", "1"),
        )
        self.assertEqual(len(result.decisions), 30)
        for file_id, number, side, within_side in expected:
            decisions = {d.field: d for d in result.decisions if d.file_id == file_id}
            for field, value in (
                ("%TRACKNUMBER%", number), ("%TOTALTRACKS%", "5"),
                ("%VINYLSIDE%", side), ("%VINYLTRACKNUMBER%", within_side),
            ):
                self.assertEqual(decisions[field].state, "SET")
                self.assertEqual(decisions[field].proposed_values, (value,))
                self.assertIsNone(decisions[field].existing_values)
            for field, value in (("%DISCNUMBER%", "1"), ("%TOTALDISCS%", "2")):
                self.assertEqual(decisions[field].state, "DELETE")
                self.assertEqual(decisions[field].existing_values, (value,))
                self.assertIsNone(decisions[field].proposed_values)
            for decision in decisions.values():
                self.assertEqual(decision.evidence.provider, "discogs")
                self.assertEqual(decision.evidence.release_id, "123")
                self.assertEqual(decision.evidence.original_value, positions[int(file_id)])
                self.assertTrue(decision.reason)
        self.assertEqual(result.issues, ())
        self.assertEqual(context.files[0].tags["%COMMENT%"], ("Keep this",))


if __name__ == "__main__":
    unittest.main()
