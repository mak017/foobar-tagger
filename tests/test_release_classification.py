from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC

from core.flac_input import inspect_release
from core.release_classification import VALID_RELEASE_TYPES
from core.rules_engine import Edition, FieldDecision, LocalFile, MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import apply_metadata
from providers.discogs import load_release
from tests.test_read_only_workflow import write_track


def fixture_edition(data: dict[str, object]) -> Edition:
    payload = {"id": 123, "title": "Afterlight", **data}
    with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode())):
        return load_release("https://www.discogs.com/release/123").edition


def tracks(*durations: str) -> list[dict[str, str]]:
    return [{"type_": "track", "title": f"Track {i}", "position": str(i), "duration": duration}
            for i, duration in enumerate(durations, 1)]


def classify_fixture(data: dict[str, object], manual: str | None = None) -> FieldDecision:
    edition = fixture_edition(data)
    local = LocalFile("selected", {"%RELEASETYPE%": ("EP",)})
    context = ReleaseContext((local,), frozenset({local.file_id}), edition, {local.file_id: edition.tracks[0].track_id},
                             manual_release_type=manual)
    proposal = propose_metadata(context, MetadataPolicy())
    return next(decision for decision in proposal.decisions if decision.field == "%RELEASETYPE%")


class ReleaseClassificationTests(unittest.TestCase):
    def test_explicit_discogs_mixed_compilation_overrides_one_track_fallback(self):
        decision = classify_fixture({
            "formats": [{"name": "CD", "descriptions": ["Album", "Compilation", "Mixed"]}],
            "tracklist": [{"type_": "track", "title": "Afterlight Mix", "position": "1", "duration": "62:00"}],
        })

        self.assertEqual((decision.state, decision.proposed_values), ("SET", ("Compilation, Mixed",)))
        self.assertEqual(decision.evidence.original_value, ("Album", "Compilation", "Mixed"))
        self.assertIn("format descriptions", decision.reason)

    def test_supported_descriptions_keep_the_exact_project_vocabulary(self):
        for descriptions, expected in (
            (["Maxi-Single"], "Single"), (["Album"], "Album"), (["Mixtape"], "Mixtape"),
            (["Sampler", "EP"], "Sampler, EP"), (["Album", "Mixed"], "Album, Mixed"),
            (["EP", "Mixed"], "EP, Mixed"),
        ):
            with self.subTest(descriptions=descriptions):
                result = classify_fixture({"formats": [{"name": "CD", "descriptions": descriptions}],
                                           "tracklist": tracks("3:00", "4:00", "4:00")})
                self.assertEqual(result.proposed_values, (expected,))

    def test_conflicting_or_unsupported_descriptions_leave_existing_type_in_review(self):
        for formats in (
            [{"name": "CD", "descriptions": ["Album", "EP"]}],
            [{"name": "CD", "descriptions": ["Album", "Partially Mixed"]}],
            [{"name": "CD", "descriptions": ["Mini-Album"]}],
            [{"name": "CD", "descriptions": ["Album", "Mixed"]}, {"name": "CD", "descriptions": ["Album"]}],
        ):
            with self.subTest(formats=formats):
                result = classify_fixture({"formats": formats, "tracklist": tracks("3:00", "4:00")})
                self.assertEqual((result.state, result.existing_values, result.proposed_values), ("UNRESOLVED", ("EP",), None))

    def test_box_set_classification_applies_to_the_full_edition(self):
        result = classify_fixture({
            "formats": [{"name": "Box Set", "descriptions": ["Compilation"]},
                        {"name": "CD", "descriptions": ["Album"]}, {"name": "CD", "descriptions": ["Album"]}],
            "tracklist": tracks("3:00", "4:00"),
        })
        self.assertEqual(result.proposed_values, ("Compilation",))

    def test_only_edition_wide_dj_mix_credit_establishes_mixing(self):
        data: dict[str, object] = {"tracklist": tracks("10:30", "10:30", "12:00", "12:00")}
        mix = classify_fixture({**data, "extraartists": [{"role": "DJ Mix", "tracks": "", "name": "DJ Test"}]})
        self.assertEqual(mix.proposed_values, ("DJ Mix",))
        self.assertEqual(mix.evidence.original_value, ("DJ Mix",))
        for credit in ({"role": "DJ Mix", "tracks": "1, 2"}, {"role": "Mixed By", "tracks": ""}):
            with self.subTest(credit=credit):
                result = classify_fixture({**data, "extraartists": [credit]})
                self.assertEqual(result.state, "UNRESOLVED")

    def test_fallback_uses_complete_tracklist_and_strict_duration_boundary(self):
        for durations, expected in (
            (("4:00", "4:00"), "Single"),
            (("10:00", "10:00", "9:59"), "EP"),
            (("10:00", "10:00", "10:00"), None),
            (("6:00", "6:00", ""), None),
            (("10:00", "10:00", "10:00", "15:00"), None),
        ):
            with self.subTest(durations=durations):
                result = classify_fixture({"tracklist": tracks(*durations)})
                self.assertEqual(result.proposed_values, (expected,) if expected else None)
        incomplete = classify_fixture({"tracklist": [*tracks("4:00"), {"type_": "index", "sub_tracks": [{}]}]})
        self.assertEqual(incomplete.state, "UNRESOLVED")

    def test_album_and_compilation_fallbacks_consider_the_full_edition(self):
        long_tracklist = tracks(*("5:00",) * 7)
        album = classify_fixture({"tracklist": long_tracklist, "artists": [{"id": 7, "name": "DJ Test"}]})
        compilation = classify_fixture({"tracklist": long_tracklist, "artists": [{"id": 194, "name": "Various"}]})
        ambiguous = classify_fixture({"tracklist": tracks("5:00", "5:00", "5:00"), "artists": [{"id": 194, "name": "Various"}]})
        self.assertEqual(album.proposed_values, ("Album",))
        self.assertEqual(compilation.proposed_values, ("Compilation",))
        self.assertEqual(ambiguous.state, "UNRESOLVED")

    def test_one_artist_can_have_different_printed_names_on_the_full_tracklist(self):
        tracklist = [
            {**track, "artists": [{"id": 7, "name": "DJ Test", "anv": "D. J. Test" if i % 2 else "DJ Test"}]}
            for i, track in enumerate(tracks(*("5:00",) * 7))
        ]
        result = classify_fixture({"tracklist": tracklist})
        self.assertEqual(result.proposed_values, ("Album",))

    def test_manual_override_accepts_each_exact_value_and_does_not_infer_from_titles(self):
        self.assertEqual(VALID_RELEASE_TYPES, (
            "Single", "Sampler", "EP", "Album", "Compilation", "Album, Mixed", "DJ Mix",
            "Compilation, Mixed", "Radioshow", "Radioshow, Live Mix", "Live Mix", "Mixtape", "Sampler, EP", "EP, Mixed",
        ))
        data = {"title": "Afterlight Radioshow Live Mixtape", "formats": [{"name": "CD", "descriptions": ["Promo"]}],
                "tracklist": tracks("10:00", "10:00", "10:00", "15:00")}
        self.assertEqual(classify_fixture(data).state, "UNRESOLVED")
        for value in VALID_RELEASE_TYPES:
            with self.subTest(value=value):
                result = classify_fixture({**data, "formats": [{"name": "CD", "descriptions": ["Album"]}]}, manual=value)
                self.assertEqual(result.proposed_values, (value,))
                self.assertEqual(result.evidence.provider, "manual")
        self.assertEqual(classify_fixture(data, manual="album").state, "UNRESOLVED")

    def test_only_approved_mapped_file_gets_release_type_on_flac_round_trip(self):
        edition = fixture_edition({"formats": [{"name": "Vinyl", "descriptions": ["Single"]}],
                                   "tracklist": tracks("7:10", "6:30")})
        with TemporaryDirectory() as folder:
            root = Path(folder)
            target, unmapped, sibling = (root / filename for filename in ("A.flac", "B.flac", "C.flac"))
            for path in (target, unmapped, sibling):
                write_track(path, "Trance")
                audio = FLAC(str(path))
                audio["RELEASETYPE"] = "Album"
                audio["X-KEEP"] = "unmanaged"
                audio.save()
            original = {path: path.read_bytes() for path in (unmapped, sibling)}
            local = inspect_release([target, unmapped], release_folder=root)
            proposal = propose_metadata(ReleaseContext(local.files, local.selected_file_ids, edition, {target.name: "1"}),
                                        MetadataPolicy())
            decisions = {decision.file_id: decision for decision in proposal.decisions if decision.field == "%RELEASETYPE%"}
            self.assertEqual(decisions[unmapped.name].state, "UNRESOLVED")
            result = apply_metadata(local, proposal, (decisions[target.name],))

            self.assertTrue(any(item.field == "%RELEASETYPE%" and item.status == "changed" for item in result.fields))
            written = FLAC(str(target))
            self.assertEqual(written["RELEASETYPE"], ["Single"])
            self.assertEqual(written["X-KEEP"], ["unmanaged"])
            self.assertEqual({path: path.read_bytes() for path in original}, original)
