from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC

from core.flac_input import inspect_release
from core.rules_engine import Edition, LocalFile, MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import apply_metadata
from providers.discogs import load_release
from tests.test_read_only_workflow import write_track


def edition_payload(artists: list[dict[str, object]], tracks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": 123, "title": " Afterlight Sessions ", "formats": [{"name": "Vinyl"}], "artists": artists,
        "tracklist": [{"type_": "track", **track} for track in tracks],
    }


def load_fixture(data: dict[str, object]) -> Edition:
    with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(data).encode())):
        return load_release("https://www.discogs.com/release/123").edition


class CreditedMetadataTests(unittest.TestCase):
    def test_mapped_track_keeps_printed_alias_and_collaborator_and_uses_full_various_edition(self):
        data = edition_payload([{"id": 194, "name": "Various", "anv": "Various Artists"}], [
            {"position": "A1", "title": "Afterlight (Night   Mix)", "artists": [
                {"id": 10, "name": "North Arcade (2)", "anv": "NORTH Arcade", "join": "feat."},
                {"id": 11, "name": "Mira, Sky & Sea", "join": ""},
            ], "extraartists": [{"name": "An Engineer", "role": "Mastered By"}]},
            {"position": "B1", "title": "Dusk", "artists": [{"id": 12, "name": "Blue Hour"}]},
        ])
        edition = load_fixture(data)
        local = LocalFile("selected", {"%ARTIST%": ("Old Credit",), "%ALBUM ARTIST%": ("Old Album Artist",)})
        sibling = LocalFile("sibling", {"%ARTIST%": ("Blue Hour",)})
        context = ReleaseContext((local, sibling), frozenset({local.file_id}), edition, {local.file_id: "1"})

        proposal = propose_metadata(context, MetadataPolicy())

        decisions = {decision.field: decision for decision in proposal.decisions}
        self.assertEqual(decisions["%ARTIST%"].proposed_values, ("NORTH Arcade feat. Mira, Sky & Sea",))
        self.assertEqual(decisions["%TITLE%"].proposed_values, ("Afterlight (Night Mix)",))
        self.assertEqual(decisions["%TITLE%"].evidence.original_value, "Afterlight (Night   Mix)")
        self.assertEqual(decisions["%ALBUM%"].proposed_values, ("Afterlight Sessions",))
        self.assertEqual(decisions["%ALBUM ARTIST%"].proposed_values, ("Various",))
        self.assertEqual(decisions["%ALBUM ARTIST%"].evidence.original_value, ("Various Artists",))
        self.assertEqual({decision.file_id for decision in proposal.decisions}, {local.file_id})

    def test_identical_full_edition_credits_delete_existing_album_artist_on_selected_flac(self):
        edition = load_fixture(edition_payload([{"id": 99, "name": "Mira, Sky & Sea (2)"}], [
            {"position": "A1", "title": "Afterlight"}, {"position": "B1", "title": "Dusk", "artists": []},
        ]))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, sibling = root / "A.flac", root / "B.flac"
            for path in (selected, sibling):
                write_track(path, "Trance", album="Old Title")
                audio = FLAC(str(path))
                audio["ARTIST"] = "Old Artist"
                audio["ALBUMARTIST"] = "Old Album Artist"
                audio["ALBUM ARTIST"] = "Old Album Artist"
                audio["X-KEEP"] = ["one", "two"]
                audio.save()
            sibling_before = sibling.read_bytes()
            local = inspect_release([selected], release_folder=root)
            context = ReleaseContext(local.files, local.selected_file_ids, edition, {selected.name: "1"})
            proposal = propose_metadata(context, MetadataPolicy())
            decisions = {decision.field: decision for decision in proposal.decisions}
            self.assertEqual(decisions["%ARTIST%"].evidence.original_value, ("Mira, Sky & Sea (2)",))
            self.assertEqual((decisions["%ALBUM ARTIST%"].state, decisions["%ALBUM ARTIST%"].proposed_values), ("DELETE", None))

            approved = tuple(decisions[field] for field in ("%ARTIST%", "%TITLE%", "%ALBUM%", "%ALBUM ARTIST%"))
            result = apply_metadata(local, proposal, approved)

            self.assertEqual({field.field for field in result.fields if field.status == "changed"},
                             {"%ARTIST%", "%TITLE%", "%ALBUM%", "%ALBUM ARTIST%"})
            written = FLAC(str(selected))
            self.assertEqual(written["ARTIST"], ["Mira, Sky & Sea"])
            self.assertEqual(written["TITLE"], ["Afterlight"])
            self.assertEqual(written["ALBUM"], ["Afterlight Sessions"])
            self.assertNotIn("ALBUMARTIST", written)
            self.assertNotIn("ALBUM ARTIST", written)
            self.assertEqual(written["X-KEEP"], ["one", "two"])
            self.assertEqual(sibling.read_bytes(), sibling_before)

    def test_distinct_discogs_artists_with_the_same_display_name_keep_the_main_release_credit(self):
        edition = load_fixture(edition_payload([{"id": 41, "name": "North Arcade"}], [
            {"position": "A1", "title": "Afterlight", "artists": [{"id": 42, "name": "Mira"}]},
            {"position": "B1", "title": "Dusk", "artists": [{"id": 43, "name": "Mira (2)"}]},
        ]))
        selected = LocalFile("a", {"%ARTIST%": ("Mira",)})
        proposal = propose_metadata(ReleaseContext((selected,), frozenset({"a"}), edition, {"a": "1"}), MetadataPolicy())
        decisions = {decision.field: decision for decision in proposal.decisions}

        self.assertEqual(decisions["%ARTIST%"].state, "KEEP")
        self.assertEqual(decisions["%ALBUM ARTIST%"].proposed_values, ("North Arcade",))

    def test_missing_separate_credit_does_not_inherit_or_change_album_artist(self):
        edition = load_fixture(edition_payload([{"id": 41, "name": "North Arcade"}], [
            {"position": "A1", "title": "Afterlight", "artists": [{"id": 42, "name": "Mira"}]},
            {"position": "B1", "title": "Dusk"},
        ]))
        local = LocalFile("selected", {"%ARTIST%": ("Old Artist",), "%ALBUM ARTIST%": ("Old Album Artist",)})
        unmapped = LocalFile("unmapped", {"%TITLE%": ("Old Track Title",)})
        context = ReleaseContext((local, unmapped), frozenset({"selected", "unmapped"}), edition, {"selected": "2"})

        proposal = propose_metadata(context, MetadataPolicy())

        decisions = {(decision.file_id, decision.field): decision for decision in proposal.decisions}
        self.assertEqual(decisions["selected", "%ARTIST%"].state, "UNRESOLVED")
        self.assertEqual(decisions["selected", "%ARTIST%"].existing_values, ("Old Artist",))
        self.assertEqual(decisions["selected", "%ALBUM ARTIST%"].state, "UNRESOLVED")
        self.assertEqual(decisions["selected", "%TITLE%"].proposed_values, ("Dusk",))
        for field in ("%ARTIST%", "%TITLE%", "%ALBUM%", "%ALBUM ARTIST%"):
            self.assertEqual(decisions["unmapped", field].state, "UNRESOLVED")

    def test_incomplete_edition_preserves_album_artist_for_review(self):
        edition = load_fixture(edition_payload([{"id": 41, "name": "North Arcade"}], [
            {"position": "A1", "title": "Afterlight", "artists": [{"id": 42, "name": "Mira"}]},
            {"type_": "index", "title": "Medley", "sub_tracks": [{"position": "B1", "title": "Dusk"}]},
        ]))
        selected = LocalFile("a", {"%ALBUM ARTIST%": ("Old Album Artist",)})
        proposal = propose_metadata(ReleaseContext((selected,), frozenset({"a"}), edition, {"a": "1"}), MetadataPolicy())
        decisions = {decision.field: decision for decision in proposal.decisions}

        self.assertFalse(edition.tracklist_complete)
        self.assertEqual(decisions["%ARTIST%"].proposed_values, ("Mira",))
        self.assertEqual(decisions["%ALBUM ARTIST%"].state, "UNRESOLVED")

    def test_known_differing_credits_establish_album_artist_even_when_a_track_artist_is_unknown(self):
        edition = load_fixture(edition_payload([{"id": 194, "name": "Various"}], [
            {"position": "A1", "title": "Afterlight", "artists": [{"id": 355, "name": "Unknown Artist"}]},
            {"position": "B1", "title": "Blue Hour", "artists": [{"id": 42, "name": "Mira"}]},
            {"position": "C1", "title": "Dusk", "artists": [{"id": 43, "name": "North Arcade"}]},
        ]))
        selected = LocalFile("a", {"%ARTIST%": ("Existing Credit",)})
        proposal = propose_metadata(ReleaseContext((selected,), frozenset({"a"}), edition, {"a": "1"}), MetadataPolicy())
        decisions = {decision.field: decision for decision in proposal.decisions}

        self.assertEqual(decisions["%ARTIST%"].state, "UNRESOLVED")
        self.assertEqual(decisions["%ARTIST%"].existing_values, ("Existing Credit",))
        self.assertEqual(decisions["%ALBUM ARTIST%"].proposed_values, ("Various",))
