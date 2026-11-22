import unittest

from core.rules_engine import Edition, EditionTrack, LocalFile, ManualDate, MetadataPolicy, ReleaseContext, propose_metadata
from ui.diff_table import ReviewApproval


class ReviewApprovalTests(unittest.TestCase):
    def test_toggling_linked_dates_does_not_toggle_independent_changes(self):
        context = ReleaseContext(
            (LocalFile("a", {}), LocalFile("b", {})), frozenset({"a", "b"}),
            Edition("discogs", "123", "Vinyl", (EditionTrack("1", "A1"), EditionTrack("2", "B1")), True),
            {"a": "1", "b": "2"}, membership_complete=True, manual_date=ManualDate("2011-03-14"),
        )
        proposal = propose_metadata(context, MetadataPolicy())
        approval = ReviewApproval(proposal)
        approval.toggle(("a", "%TRACKNUMBER%"))
        approval.toggle(("a", "%DATE%"))

        self.assertEqual({(decision.file_id, decision.field) for decision in approval.selected()}, {
            ("a", "%DATE%"), ("a", "%RELEASE DATE%"), ("b", "%DATE%"), ("b", "%RELEASE DATE%"),
            ("a", "%TRACKNUMBER%"),
        })
        approval.toggle(("b", "%RELEASE DATE%"))
        self.assertEqual([(decision.file_id, decision.field) for decision in approval.selected()], [("a", "%TRACKNUMBER%")])
        self.assertEqual(ReviewApproval(proposal).selected(), ())
