from dataclasses import replace
import unittest

from core.matching import suggest_track_mappings
from core.rules_engine import Edition, EditionTrack, LocalFile, ReleaseContext


class TrackMatchingTests(unittest.TestCase):
    def test_uses_title_version_duration_and_position_to_suggest_a_single_track(self):
        file = LocalFile("A1 - Afterlight.flac", {
            "%TITLE%": ("Afterlight [Original Mix]",), "%TRACKNUMBER%": ("1/2",),
            "%VINYLSIDE%": ("A",), "%VINYLTRACKNUMBER%": ("1",),
        }, 368)
        edition = Edition("discogs", "123", "Vinyl", (
            EditionTrack("a", "A1", "Afterlight (Original Mix)", 363),
            EditionTrack("b", "B1", "Afterlight (Radio Edit)", 368),
        ), tracklist_complete=True)
        context = ReleaseContext((file,), frozenset({file.file_id}), edition, {})

        (result,) = suggest_track_mappings(context)

        self.assertEqual((result.state, result.suggested_id, result.title_source), ("suggested", "a", "title tag"))
        self.assertEqual([(c.track.track_id, c.title, c.duration_difference, c.position) for c in result.candidates], [
            ("a", "match", 5, "match"), ("b", "version_conflict", 0, "conflict"),
        ])
        self.assertEqual(dict(context.track_mapping), {})

    def test_missing_version_and_outlying_duration_stay_unresolved(self):
        edition = Edition("discogs", "123", "Vinyl", (
            EditionTrack("a", "A1", "Afterlight (Original Mix)", 360),
            EditionTrack("b", "B1", "Blue Hour", 285),
        ), tracklist_complete=True)
        files = (
            LocalFile("A1 Afterlight.flac", {"%TITLE%": ("Afterlight",)}, 360),
            LocalFile("B1 Blue Hour.flac", {"%TITLE%": ("Blue Hour",)}, 290.1),
        )
        first, second = suggest_track_mappings(ReleaseContext(files, frozenset(f.file_id for f in files), edition, {}))

        self.assertEqual((first.state, first.candidates[0].title), ("unresolved", "version_missing"))
        self.assertEqual((second.state, second.candidates[0].position), ("unresolved", "match"))
        self.assertGreater(second.candidates[0].duration_difference or 0, 5)

    def test_similar_lengths_stay_ambiguous_until_a_matching_side_disambiguates(self):
        edition = Edition("discogs", "123", "Vinyl", (
            EditionTrack("a", "A1", "Afterlight (Dub Mix)", 360),
            EditionTrack("b", "B1", "Afterlight - Dub Mix", 362),
        ), tracklist_complete=True)
        file = LocalFile("Afterlight.flac", {"%TITLE%": ("Afterlight (Dub Mix)",)}, 361)
        context = ReleaseContext((file,), frozenset({file.file_id}), edition, {})

        (ambiguous,) = suggest_track_mappings(context)
        sided = replace(file, tags={**file.tags, "%VINYLSIDE%": ("B",), "%VINYLTRACKNUMBER%": ("1",)})
        (resolved,) = suggest_track_mappings(replace(context, files=(sided,)))

        self.assertEqual(ambiguous.state, "ambiguous")
        self.assertEqual([c.track.track_id for c in ambiguous.candidates if c.plausible], ["a", "b"])
        self.assertEqual((resolved.state, resolved.suggested_id), ("suggested", "b"))

    def test_rejects_duplicate_claims_and_keeps_confirmed_manual_mappings(self):
        edition = Edition("discogs", "123", "WEB", (EditionTrack("a", "1", "Afterlight", 360),), True)
        files = tuple(LocalFile(name, {"%TITLE%": ("Afterlight",)}, 360)
                      for name in ("First.flac", "Second.flac", "Context.flac"))
        context = ReleaseContext(files, frozenset({"First.flac", "Second.flac"}), edition, {})

        duplicate = suggest_track_mappings(context)
        (remaining,) = suggest_track_mappings(replace(context, track_mapping={"First.flac": "a"}))
        (single,) = suggest_track_mappings(replace(context, selected_file_ids=frozenset({"Second.flac"})))

        self.assertEqual([(r.file_id, r.state) for r in duplicate], [("First.flac", "ambiguous"), ("Second.flac", "ambiguous")])
        self.assertEqual((remaining.file_id, remaining.state), ("Second.flac", "unresolved"))
        self.assertTrue(remaining.candidates[0].claimed)
        self.assertEqual(single.suggested_id, "a")

    def test_filename_disc_position_can_support_an_exact_title_without_duration(self):
        edition = Edition("discogs", "123", "CD", (
            EditionTrack("first_disc", "1-03", "Afterlight"),
            EditionTrack("second_disc", "2-03", "Afterlight"),
        ), tracklist_complete=True)
        file = LocalFile("Disc 2/2-03 - Afterlight.flac", {})

        (result,) = suggest_track_mappings(ReleaseContext((file,), frozenset({file.file_id}), edition, {}))

        self.assertEqual((result.state, result.suggested_id, result.title_source), ("suggested", "second_disc", "filename"))

    def test_conflicting_positions_and_missing_corroboration_stay_unresolved(self):
        edition = Edition("discogs", "123", "Vinyl", (EditionTrack("a", "A1", "Afterlight", 360),), True)
        tagged = LocalFile("A1 - Afterlight.flac", {"%TITLE%": ("Afterlight",), "%VINYLSIDE%": ("B",)}, 360)
        title_only = LocalFile("Afterlight.flac", {"%TITLE%": ("Afterlight",)})

        for file, expected_position in ((tagged, "conflict"), (title_only, "unknown")):
            with self.subTest(file=file.file_id):
                (result,) = suggest_track_mappings(ReleaseContext((file,), frozenset({file.file_id}), edition, {}))
                self.assertEqual((result.state, result.candidates[0].position), ("unresolved", expected_position))

    def test_complete_edition_ordinal_can_corroborate_a_vinyl_track_number(self):
        edition = Edition("discogs", "123", "Vinyl", (
            EditionTrack("a", "A1", "Afterlight"), EditionTrack("b", "B1", "Blue Hour"),
        ), tracklist_complete=True)
        file = LocalFile("Blue Hour.flac", {"%TITLE%": ("Blue Hour",), "%TRACKNUMBER%": ("2",)})
        context = ReleaseContext((file,), frozenset({file.file_id}), edition, {})

        (complete,) = suggest_track_mappings(context)
        (incomplete,) = suggest_track_mappings(replace(context, edition=replace(edition, tracklist_complete=False)))

        self.assertEqual(complete.suggested_id, "b")
        self.assertEqual((incomplete.state, incomplete.candidates[0].position), ("unresolved", "unknown"))

    def test_incomplete_tracklist_needs_manual_confirmation_even_for_strong_evidence(self):
        edition = Edition("discogs", "123", "Vinyl", (EditionTrack("a", "A1", "Afterlight", 360),), False)
        file = LocalFile("A1 Afterlight.flac", {"%TITLE%": ("Afterlight",)}, 360)

        (result,) = suggest_track_mappings(ReleaseContext((file,), frozenset({file.file_id}), edition, {}))

        self.assertEqual((result.state, result.suggested_id), ("unresolved", None))
        self.assertIn("incomplete", result.reason)
