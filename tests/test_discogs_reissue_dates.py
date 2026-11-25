from io import BytesIO
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from core.rules_engine import LocalFile, MetadataPolicy, MetadataProposal, ReleaseContext, propose_metadata
from providers.discogs import load_release


def release_payload(release_id: int, released: str, tracks: tuple[tuple[str, str, str], ...]) -> dict[str, object]:
    return {"id": release_id, "title": "Afterlight", "master_id": 7, "released": released,
            "artists": [{"id": 12, "name": "Afterlight"}], "tracklist": [
                {"type_": "track", "position": position, "title": title, "duration": duration}
                for position, title, duration in tracks
            ]}


def preview_dates(
    selected: dict[str, object], original: dict[str, object], versions: list[dict[str, object]] | None,
    pages: int = 1,
) -> MetadataProposal:
    def response(req: Request, *, timeout: int) -> BytesIO:
        url = req.full_url
        if url.endswith("/releases/20"):
            payload = selected
        elif url.endswith("/releases/10"):
            payload = original
        elif url.startswith("https://api.discogs.com/masters/7/versions?"):
            if versions is None:
                raise URLError("offline")
            payload = {"pagination": {"page": 1, "pages": pages}, "versions": versions}
        else:
            raise AssertionError(f"Unexpected Discogs request {url}")
        return BytesIO(json.dumps(payload).encode())

    with patch("urllib.request.urlopen", side_effect=response):
        edition = load_release("https://www.discogs.com/release/20").edition
    local = LocalFile("Afterlight.flac", {"%DATE%": ("2019",)})
    return propose_metadata(ReleaseContext(
        (local,), frozenset({local.file_id}), edition, {local.file_id: edition.tracks[0].track_id},
        membership_complete=True,
    ), MetadataPolicy())


class DiscogsReissueDateTests(unittest.TestCase):
    def test_unchanged_remaster_uses_original_edition_date(self):
        original = release_payload(10, "1998-02-03", (("A", "Afterlight (Vocal Mix)", "6:24"),))
        selected = release_payload(20, "2020-06-12", (("1", "Afterlight (Vocal Mix) [2020 Remaster]", "6:25"),))
        proposal = preview_dates(selected, original, [{"id": 10, "released": "1998"}, {"id": 20, "released": "2020"}])

        self.assertEqual([(d.field, d.proposed_values, d.evidence.release_id) for d in proposal.decisions
                          if d.field in ("%DATE%", "%RELEASE DATE%")],
                         [("%DATE%", ("1998",), "10"), ("%RELEASE DATE%", ("1998-02-03",), "10")])

    def test_bonus_track_substitution_and_new_mix_each_use_selected_edition_date(self):
        original_tracks = (("A", "Afterlight (Vocal Mix)", "6:24"), ("B", "Blue Hour", "7:20"))
        original = release_payload(10, "1998", original_tracks)
        versions: list[dict[str, object]] = [
            {"id": 5, "released": "1997", "format": "Vinyl, Promo"},
            {"id": 10, "released": "1998"}, {"id": 20, "released": "2020", "format": "CD, Reissue"},
        ]
        for tracks in (
            (*original_tracks, ("C", "Morning Light", "5:20")),
            (original_tracks[0], ("B", "Morning Light", "5:20")),
            (("A", "Afterlight (Club Mix)", "6:24"), original_tracks[1]),
        ):
            with self.subTest(tracks=tracks):
                selected = release_payload(20, "2020-06-12", tracks)
                proposal = preview_dates(selected, original, versions)
                self.assertEqual([(d.proposed_values, d.evidence.release_id) for d in proposal.decisions
                                  if d.field in ("%DATE%", "%RELEASE DATE%")],
                                 [(("2020",), "20"), (("2020-06-12",), "20")])

    def test_approximate_original_date_sets_only_the_year(self):
        tracks = (("1", "Afterlight", "6:24"),)
        original = release_payload(10, "1998-02-00", tracks)
        selected = release_payload(20, "2020-06-12", tracks)
        proposal = preview_dates(selected, original, [{"id": 10, "released": "1998"}, {"id": 20, "released": "2020"}])

        self.assertEqual([(d.state, d.proposed_values) for d in proposal.decisions if d.field in ("%DATE%", "%RELEASE DATE%")],
                         [("SET", ("1998",)), ("KEEP", None)])

    def test_selecting_the_earliest_edition_uses_its_date_without_a_second_release_lookup(self):
        selected = release_payload(20, "1998-02-03", (("1", "Afterlight", ""),))
        proposal = preview_dates(selected, {}, [{"id": 20, "released": "1998"}, {"id": 30, "released": "2020"}])

        self.assertEqual([(d.proposed_values, d.evidence.release_id) for d in proposal.decisions
                          if d.field in ("%DATE%", "%RELEASE DATE%")], [(("1998",), "20"), (("1998-02-03",), "20")])

    def test_incomplete_comparison_does_not_infer_recording_identity(self):
        original = release_payload(10, "1998-02-03", (("A", "Afterlight (Vocal Mix)", "6:24"),))
        for title, duration in (("Afterlight", "6:24"), ("Afterlight (Vocal Mix)", ""), ("Afterlight (Vocal Mix)", "7:00")):
            with self.subTest(title=title, duration=duration):
                selected = release_payload(20, "2020-06-12", (("1", title, duration),))
                proposal = preview_dates(selected, original, [{"id": 10, "released": "1998"}])

                self.assertEqual([(d.state, d.existing_values, d.proposed_values) for d in proposal.decisions
                                  if d.field == "%DATE%"], [("UNRESOLVED", ("2019",), None)])

    def test_failed_or_ambiguous_original_lookup_keeps_the_rest_of_the_proposal(self):
        tracks = (("1", "Afterlight", "6:24"),)
        selected = release_payload(20, "2020-06-12", tracks)
        original = release_payload(10, "1998-02-03", tracks)
        for versions, pages in (
            (None, 1), ([{"id": 10, "released": "1998"}, {"id": 11, "released": "1998-05-03"}], 1),
            ([{"id": 10, "released": "1998"}], 2),
        ):
            with self.subTest(versions=versions, pages=pages):
                proposal = preview_dates(selected, original, versions, pages)

                self.assertEqual([(d.state, d.existing_values) for d in proposal.decisions if d.field == "%DATE%"],
                                 [("UNRESOLVED", ("2019",))])
                self.assertTrue(any(d.field == "%TITLE%" and d.proposed_values == ("Afterlight",) for d in proposal.decisions))
