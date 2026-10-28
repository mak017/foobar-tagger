import unittest

from core.rules_engine import (
    Edition, EditionTrack, GenreSource, LocalFile, MetadataPolicy,
    ReleaseContext, propose_metadata,
)


def release_context(sources, files=None, selected=None, complete=True):
    if files is None:
        files = (LocalFile("a", {}), LocalFile("b", {}))
    return ReleaseContext(
        files=files,
        selected_file_ids=frozenset(selected if selected is not None else (f.file_id for f in files)),
        edition=Edition("discogs", "123", "WEB", (
            EditionTrack("ta", "1"), EditionTrack("tb", "2"),
        ), True, genre_sources=sources),
        track_mapping={"a": "ta", "b": "tb"},
        membership_complete=complete,
    )


class GenreTests(unittest.TestCase):
    def test_unresolved_mapping_withholds_release_genres_without_partial_changes(self):
        from dataclasses import replace

        context = replace(
            release_context((GenreSource("discogs", "123", ("Trance",)),)),
            track_mapping={"a": "ta"},
        )
        result = propose_metadata(context, MetadataPolicy(frozenset({"Trance"})))
        self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
        self.assertEqual(result.issues[0].code, "GENRE_MAPPING_UNCONFIRMED")
        self.assertEqual(result.approval_groups, ())

    def test_invalid_policy_cannot_admit_empty_or_comma_containing_style_names(self):
        from core.rules_engine import InvalidMetadataPolicy

        for accepted in (frozenset({""}), frozenset({"Trance, Techno"}), frozenset({" Trance"})):
            with self.subTest(accepted=accepted):
                with self.assertRaises(InvalidMetadataPolicy):
                    propose_metadata(release_context(()), MetadataPolicy(accepted))
        for mappings in ({"": "Trance"}, {"Store": ""}, {"Store": "Trance, Techno"}):
            with self.subTest(mappings=mappings):
                with self.assertRaises(InvalidMetadataPolicy):
                    propose_metadata(release_context(()), MetadataPolicy(frozenset({"Trance"}), mappings))

    def test_manual_genres_override_sources_but_still_require_vocabulary_and_consistency(self):
        from dataclasses import replace

        context = release_context((GenreSource("discogs", "123", ("Trance",)),))
        context = replace(context, manual_genres=("Techno", "Techno"))
        policy = MetadataPolicy(frozenset({"Trance", "Techno"}))
        result = propose_metadata(context, policy)
        self.assertEqual([d.proposed_values for d in result.decisions], [("Techno",), ("Techno",)])
        self.assertTrue(all(d.evidence.provider == "manual" for d in result.decisions))
        invalid = propose_metadata(replace(context, manual_genres=("Invalid",)), policy)
        self.assertTrue(all(d.state == "UNRESOLVED" for d in invalid.decisions))
        self.assertEqual(invalid.issues[0].code, "GENRE_STYLE_NOT_ACCEPTED")
        partial = replace(context, selected_file_ids=frozenset({"a"}))
        self.assertEqual(propose_metadata(partial, policy).issues[0].code, "GENRE_SELECTION_CONFLICT")

    def test_conflicting_sources_require_selection_instead_of_first_source_winning(self):
        for sources in (
            (GenreSource("discogs", "123", ("Trance",)), GenreSource("discogs", "123", ("Techno",))),
            (GenreSource("beatport", "456", ("Melodic House & Techno",)),
             GenreSource("other", "789", ("Other category",))),
        ):
            with self.subTest(sources=sources):
                result = propose_metadata(release_context(sources), MetadataPolicy(
                    frozenset({"Trance", "Techno", "Progressive House"}),
                ))
                self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
                self.assertEqual(result.issues[0].code, "GENRE_SOURCE_CONFLICT")
                self.assertEqual(result.approval_groups, ())

    def test_genre_changes_are_grouped_and_matching_sibling_needs_no_write(self):
        source = (GenreSource("discogs", "123", ("Trance",)),)
        context = release_context(source)
        result = propose_metadata(context, MetadataPolicy(frozenset({"Trance"})))
        self.assertEqual(len(result.approval_groups), 1)
        group = result.approval_groups[0]
        self.assertEqual(group.field, "%GENRE%")
        self.assertEqual(group.file_ids, ("a", "b"))
        self.assertEqual(group.target_values, ("Trance",))
        partial = release_context(source, files=(
            LocalFile("a", {}), LocalFile("b", {"%GENRE%": ("Trance",)}),
        ), selected=("a",))
        proposal = propose_metadata(partial, MetadataPolicy(frozenset({"Trance"})))
        self.assertEqual([(d.file_id, d.state) for d in proposal.decisions], [("a", "SET")])
        self.assertEqual(proposal.approval_groups[0].file_ids, ("a",))

    def test_incomplete_membership_withholds_release_wide_genre(self):
        context = release_context((GenreSource("discogs", "123", ("Trance",)),), complete=False)
        result = propose_metadata(context, MetadataPolicy(frozenset({"Trance"})))
        self.assertTrue(all(d.state == "UNRESOLVED" for d in result.decisions))
        self.assertEqual(result.issues[0].code, "MEMBERSHIP_INCOMPLETE")
        self.assertIn("refresh_membership", result.issues[0].options)

    def test_partial_selection_blocks_genre_change_when_sibling_differs(self):
        context = release_context((GenreSource("discogs", "123", ("Trance",)),), files=(
            LocalFile("a", {"%GENRE%": ("House",)}),
            LocalFile("b", {"%GENRE%": ("Techno",)}),
        ), selected=("a",))
        result = propose_metadata(context, MetadataPolicy(frozenset({"Trance"})))
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.decisions[0].file_id, "a")
        self.assertEqual(result.decisions[0].state, "UNRESOLVED")
        self.assertEqual(result.decisions[0].existing_values, ("House",))
        self.assertIsNone(result.decisions[0].proposed_values)
        self.assertEqual(result.issues[0].code, "GENRE_SELECTION_CONFLICT")
        self.assertIn("b", result.issues[0].file_ids)
        self.assertIn("expand_selection", result.issues[0].options)

    def test_missing_unmapped_or_unaccepted_styles_preserve_existing_genre(self):
        cases = (
            ((), "GENRE_EVIDENCE_REQUIRED"),
            ((GenreSource("beatport", "456", ("Unmapped category",)),), "GENRE_MAPPING_REQUIRED"),
            ((GenreSource("discogs", "123", ("Unaccepted style",)),), "GENRE_STYLE_NOT_ACCEPTED"),
            ((GenreSource("beatport", "456", ("Melodic House & Techno", "Unmapped category")),),
             "GENRE_MAPPING_REQUIRED"),
        )
        for sources, code in cases:
            with self.subTest(code=code, sources=sources):
                context = release_context(sources, files=(
                    LocalFile("a", {"%GENRE%": ("Old genre",)}), LocalFile("b", {}),
                ))
                result = propose_metadata(context, MetadataPolicy(frozenset({"Progressive House"})))
                self.assertEqual([d.state for d in result.decisions], ["UNRESOLVED", "UNRESOLVED"])
                self.assertTrue(all(d.proposed_values is None for d in result.decisions))
                self.assertEqual(result.decisions[0].existing_values, ("Old genre",))
                self.assertEqual(result.issues[0].code, code)
                self.assertIn("select_genres", result.issues[0].options)

    def test_approved_store_mapping_is_used_only_without_discogs_styles(self):
        context = release_context((
            GenreSource("discogs", "123", ()),
            GenreSource("beatport", "456", ("Melodic House & Techno",)),
        ))
        result = propose_metadata(context, MetadataPolicy(frozenset({"Progressive House"})))
        self.assertEqual([d.proposed_values for d in result.decisions], [
            ("Progressive House",), ("Progressive House",),
        ])
        self.assertEqual(result.decisions[0].evidence.provider, "beatport")
        self.assertEqual(result.decisions[0].evidence.original_value, ("Melodic House & Techno",))
        self.assertIn("mapping", result.decisions[0].reason)

    def test_discogs_styles_are_one_ordered_value_on_every_track(self):
        source = GenreSource("discogs", "123", (
            "Trance", "Progressive Trance", "Trance", "Progressive House", "Tech Trance",
        ))
        context = release_context((source, GenreSource("beatport", "456", ("Techno",))))
        result = propose_metadata(context, MetadataPolicy(accepted_styles=frozenset({
            "Trance", "Progressive Trance", "Progressive House", "Tech Trance", "Techno",
        })))
        self.assertEqual([(d.file_id, d.state, d.proposed_values) for d in result.decisions], [
            ("a", "SET", ("Trance, Progressive Trance, Progressive House, Tech Trance",)),
            ("b", "SET", ("Trance, Progressive Trance, Progressive House, Tech Trance",)),
        ])
        self.assertTrue(all(d.field == "%GENRE%" for d in result.decisions))
        self.assertEqual(result.decisions[0].evidence.provider, "discogs")
        self.assertEqual(result.decisions[0].evidence.release_id, "123")
        self.assertEqual(result.decisions[0].evidence.original_value, source.styles)
        self.assertIn("genres", result.evaluated_rules)


if __name__ == "__main__":
    unittest.main()
