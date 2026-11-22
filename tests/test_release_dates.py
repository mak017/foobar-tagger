import unittest
from typing import cast

from core.rules_engine import (
    DateSource, Edition, EditionTrack, LocalFile, MetadataPolicy,
    ReleaseContext, ReleaseDates, propose_metadata,
)


def date_context(dates, files=None, selected=None, complete=True):
    files = files if files is not None else (LocalFile("a", {}), LocalFile("b", {}))
    return ReleaseContext(
        files=files,
        selected_file_ids=frozenset(selected if selected is not None else (f.file_id for f in files)),
        edition=Edition("discogs", "edition", "WEB", (
            EditionTrack("ta", "1"), EditionTrack("tb", "2"),
        ), True, dates=dates),
        track_mapping={"a": "ta", "b": "tb"},
        membership_complete=complete,
    )


class ReleaseDateTests(unittest.TestCase):
    def test_manual_deletion_respects_selection_and_rejects_contradictory_command(self):
        from dataclasses import replace
        from core.rules_engine import ManualDate

        context = date_context(ReleaseDates(), files=(
            LocalFile("a", {"%DATE%": ("2020",), "%RELEASE DATE%": ("2020-01-01",)}),
            LocalFile("b", {"%DATE%": ("2020",), "%RELEASE DATE%": ("2020-01-01",)}),
        ), selected=("a",))
        blocked = propose_metadata(replace(context, manual_date=ManualDate("2020", True)), MetadataPolicy())
        self.assertTrue(all(d.state == "UNRESOLVED" for d in blocked.decisions))
        self.assertEqual(blocked.issues[0].code, "DATE_SELECTION_CONFLICT")
        invalid = propose_metadata(replace(context, manual_date=ManualDate("2020-07-01", True)), MetadataPolicy())
        self.assertEqual(invalid.issues[0].code, "DATE_MANUAL_CONFLICT")
        self.assertTrue(all(d.state == "UNRESOLVED" for d in invalid.decisions))

    def test_agreeing_dates_need_no_relationship_guess_or_spurious_changes(self):
        dates = ReleaseDates(
            DateSource("discogs", "edition", "2020-06-12"),
            DateSource("discogs", "original", "2020-06-12"), "unknown",
        )
        context = date_context(dates, files=(
            LocalFile("a", {"%DATE%": ("2020",), "%RELEASE DATE%": ("2020-06-12",)}),
            LocalFile("b", {"%DATE%": ("2020",), "%RELEASE DATE%": ("2020-06-12",)}),
        ), selected=("a",))
        result = propose_metadata(context, MetadataPolicy())
        self.assertEqual([d.state for d in result.decisions], ["KEEP", "KEEP"])
        self.assertEqual({d.file_id for d in result.decisions}, {"a"})
        self.assertEqual(result.approval_groups, ())
        self.assertEqual(result.issues, ())

    def test_manual_date_can_override_evidence_or_explicitly_clear_exact_date(self):
        from dataclasses import replace
        from core.rules_engine import ManualDate

        dates = ReleaseDates(DateSource("discogs", "edition", "2020-06-12"), content_relationship="original")
        context = date_context(dates, files=(
            LocalFile("a", {"%RELEASE DATE%": ("2020-06",)}),
            LocalFile("b", {"%RELEASE DATE%": ("2020-06",)}),
        ))
        full = propose_metadata(replace(context, manual_date=ManualDate("2020-07-01")), MetadataPolicy())
        self.assertTrue(all(d.evidence.provider == "manual" for d in full.decisions))
        self.assertEqual(full.decisions[1].proposed_values, ("2020-07-01",))
        cleared = propose_metadata(replace(context, manual_date=ManualDate("2020", clear_exact_date=True)), MetadataPolicy())
        self.assertEqual([(d.field, d.state, d.proposed_values) for d in cleared.decisions], [
            ("%DATE%", "SET", ("2020",)), ("%RELEASE DATE%", "DELETE", None),
            ("%DATE%", "SET", ("2020",)), ("%RELEASE DATE%", "DELETE", None),
        ])
        self.assertIn(("a", "%RELEASE DATE%", None), cleared.approval_groups[0].linked_changes)
        invalid = propose_metadata(replace(context, manual_date=ManualDate("2020-07")), MetadataPolicy())
        self.assertTrue(all(d.state == "UNRESOLVED" for d in invalid.decisions))

    def test_year_only_evidence_preserves_existing_exact_dates_for_review(self):
        dates = ReleaseDates(DateSource("discogs", "edition", "2020"), content_relationship="original")
        for existing in ("2020-06-12", "2020-06", "2019-01-01"):
            with self.subTest(existing=existing):
                context = date_context(dates, files=(
                    LocalFile("a", {"%DATE%": ("2020",), "%RELEASE DATE%": (existing,)}),
                    LocalFile("b", {"%DATE%": ("2020",), "%RELEASE DATE%": (existing,)}),
                ))
                result = propose_metadata(context, MetadataPolicy())
                exact = [d for d in result.decisions if d.field == "%RELEASE DATE%"]
                self.assertTrue(all(d.state == "UNRESOLVED" for d in exact))
                self.assertTrue(all(d.existing_values == (existing,) for d in exact))
                self.assertTrue(all(d.proposed_values is None for d in exact))
                self.assertTrue(result.issues)
                self.assertFalse(any(d.state == "DELETE" for d in result.decisions))

    def test_year_and_exact_date_changes_share_one_approval_group(self):
        dates = ReleaseDates(DateSource("discogs", "edition", "2020-02-29"), content_relationship="original")
        result = propose_metadata(date_context(dates), MetadataPolicy())
        self.assertEqual(len(result.approval_groups), 1)
        group = result.approval_groups[0]
        self.assertEqual(group.file_ids, ("a", "b"))
        self.assertEqual(group.linked_changes, (
            ("a", "%DATE%", ("2020",)), ("a", "%RELEASE DATE%", ("2020-02-29",)),
            ("b", "%DATE%", ("2020",)), ("b", "%RELEASE DATE%", ("2020-02-29",)),
        ))

    def test_date_changes_respect_membership_mapping_and_unselected_siblings(self):
        from dataclasses import replace

        dates = ReleaseDates(DateSource("discogs", "edition", "2020-06-12"), content_relationship="original")
        base = date_context(dates)
        cases = (
            (replace(base, membership_complete=False), "MEMBERSHIP_INCOMPLETE"),
            (replace(base, track_mapping={"a": "ta"}), "DATE_MAPPING_UNCONFIRMED"),
            (replace(base, selected_file_ids=frozenset({"a"})), "DATE_SELECTION_CONFLICT"),
        )
        for context, code in cases:
            with self.subTest(code=code):
                result = propose_metadata(context, MetadataPolicy())
                self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
                self.assertTrue(all(d.proposed_values is None for d in result.decisions))
                self.assertEqual(result.issues[0].code, code)
                self.assertEqual(result.approval_groups, ())
                if code == "DATE_SELECTION_CONFLICT":
                    self.assertIn("b", result.issues[0].file_ids)
                    self.assertIn("expand_selection", result.issues[0].options)

    def test_partial_invalid_or_impossible_source_dates_are_unresolved(self):
        for value in ("2020-06", "2020-00-00", "2021-02-29", "2020-13-01", "0000", "2020-6-1", "", " 2020", 2020, None):
            with self.subTest(value=value):
                # Intentionally violate the input type to verify runtime validation.
                dates = ReleaseDates(DateSource("discogs", "edition", cast(str, value)), content_relationship="original")
                result = propose_metadata(date_context(dates), MetadataPolicy())
                self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
                self.assertTrue(all(d.proposed_values is None for d in result.decisions))
                self.assertEqual(result.issues[0].code, "DATE_VALUE_INVALID")

    def test_year_only_evidence_sets_year_without_inventing_exact_date(self):
        context = date_context(ReleaseDates(
            DateSource("discogs", "edition", "2020"), content_relationship="original",
        ))
        result = propose_metadata(context, MetadataPolicy())
        self.assertEqual([(d.field, d.state, d.proposed_values) for d in result.decisions], [
            ("%DATE%", "SET", ("2020",)), ("%RELEASE DATE%", "KEEP", None),
            ("%DATE%", "SET", ("2020",)), ("%RELEASE DATE%", "KEEP", None),
        ])

    def test_uncertain_relationship_or_missing_date_preserves_existing_values(self):
        cases = (
            ReleaseDates(DateSource("discogs", "edition", "2020-06-12"),
                         DateSource("discogs", "original", "1998-02-03"), "unknown"),
            ReleaseDates(DateSource("discogs", "edition", "2020-06-12"), None, "unchanged"),
            ReleaseDates(),
        )
        for dates in cases:
            with self.subTest(dates=dates):
                context = date_context(dates, files=(
                    LocalFile("a", {"%DATE%": ("2001",), "%RELEASE DATE%": ("2001-01-02",)}),
                    LocalFile("b", {}),
                ))
                result = propose_metadata(context, MetadataPolicy())
                self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
                self.assertTrue(all(d.proposed_values is None for d in result.decisions))
                self.assertEqual(result.decisions[0].existing_values, ("2001",))
                self.assertTrue(result.issues)
                self.assertEqual(result.approval_groups, ())

    def test_unchanged_reissue_uses_supported_original_date(self):
        dates = ReleaseDates(
            edition_date=DateSource("discogs", "reissue", "2020-06-12"),
            original_date=DateSource("discogs", "original", "1998-02-03"),
            content_relationship="unchanged",
        )
        result = propose_metadata(date_context(dates), MetadataPolicy())
        self.assertEqual([d.proposed_values for d in result.decisions], [
            ("1998",), ("1998-02-03",), ("1998",), ("1998-02-03",),
        ])
        self.assertTrue(all(d.evidence.release_id == "original" for d in result.decisions))
        self.assertTrue(all("unchanged" in d.reason for d in result.decisions))

    def test_changed_content_uses_edition_date_for_every_track(self):
        dates = ReleaseDates(
            edition_date=DateSource("discogs", "edition", "2020-06-12"),
            original_date=DateSource("discogs", "original", "1998-02-03"),
            content_relationship="changed",
        )
        result = propose_metadata(date_context(dates), MetadataPolicy())
        self.assertEqual([(d.file_id, d.field, d.state, d.proposed_values) for d in result.decisions], [
            ("a", "%DATE%", "SET", ("2020",)),
            ("a", "%RELEASE DATE%", "SET", ("2020-06-12",)),
            ("b", "%DATE%", "SET", ("2020",)),
            ("b", "%RELEASE DATE%", "SET", ("2020-06-12",)),
        ])
        self.assertTrue(all(d.evidence.release_id == "edition" for d in result.decisions))
        self.assertTrue(all(d.evidence.original_value == "2020-06-12" for d in result.decisions))
        self.assertTrue(all("changed" in d.reason for d in result.decisions))
        self.assertIn("release_dates", result.evaluated_rules)
        self.assertEqual(result.issues, ())


if __name__ == "__main__":
    unittest.main()
