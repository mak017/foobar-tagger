from email.message import Message
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from providers.discogs import DiscogsError, ReleaseQuery, load_release, search_releases


class DiscogsSearchTests(unittest.TestCase):
    def test_uses_edition_filters_and_returns_distinct_editions_on_requested_page(self):
        release = {"type": "release", "id": 123, "title": "A & B - Afterlight", "catno": "NA-07",
                   "format": ["Vinyl", "LP"], "label": ["Night Arc"], "country": "Germany", "year": "2011"}
        payload = {"pagination": {"page": 2, "pages": 3}, "results": [
            release, {**release, "id": 456, "format": ["File"], "country": "Worldwide", "year": 2019},
            {**release, "type": "master", "id": 789}, {**release, "uri": "https://example.org/"},
        ]}
        with patch.dict(os.environ, {"DISCOGS_TOKEN": "fixture-token"}), patch(
            "urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode()),
        ) as fetch:
            results = search_releases(ReleaseQuery(catalog=" NA-07 ", artist="A & B", title="Afterlight"), page=2)

        req = fetch.call_args.args[0]
        self.assertEqual(urlsplit(req.full_url).path, "/database/search")
        self.assertEqual(parse_qs(urlsplit(req.full_url).query), {
            "type": ["release"], "page": ["2"], "per_page": ["25"], "catno": ["NA-07"],
            "artist": ["A & B"], "release_title": ["Afterlight"],
        })
        self.assertIsNotNone(req.get_header("Authorization"))
        self.assertEqual((results.page, results.pages), (2, 3))
        self.assertEqual([(item.release_id, item.formats, item.year) for item in results.candidates], [
            ("123", ("Vinyl", "LP"), "2011"), ("456", ("File",), "2019"),
        ])
        self.assertEqual(results.candidates[0].url, "https://www.discogs.com/release/123")

    def test_supports_anonymous_search_when_discogs_allows_it(self):
        payload = {"pagination": {"page": 1, "pages": 0}, "results": []}
        with patch.dict(os.environ, {"DISCOGS_TOKEN": ""}), patch(
            "urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode()),
        ) as fetch:
            result = search_releases(ReleaseQuery(text="Afterlight"))
        self.assertIsNone(fetch.call_args.args[0].get_header("Authorization"))
        self.assertEqual(result.candidates, ())

    def test_reports_authentication_denial_and_rate_limit_without_inventing_an_edition(self):
        denied = HTTPError("https://api.discogs.com/database/search", 401, "Unauthorized", Message(), None)
        with patch.dict(os.environ, {"DISCOGS_TOKEN": ""}), patch("urllib.request.urlopen", side_effect=denied):
            with self.assertRaisesRegex(DiscogsError, "DISCOGS_TOKEN"):
                search_releases(ReleaseQuery(text="Afterlight"))
        rate_limit = HTTPError("https://api.discogs.com/database/search", 429, "Too Many Requests", Message(), None)
        with patch.dict(os.environ, {"DISCOGS_TOKEN": "fixture-token"}), patch("urllib.request.urlopen", side_effect=rate_limit):
            with self.assertRaisesRegex(DiscogsError, "rate limit"):
                search_releases(ReleaseQuery(text="Afterlight"))

    def test_selected_edition_load_keeps_known_durations_and_leaves_invalid_values_unknown(self):
        data = {"id": 456, "title": "Afterlight", "tracklist": [
            {"type_": "track", "position": "A1", "title": "Afterlight", "duration": "6:05"},
            {"type_": "track", "position": "B1", "title": "Afterlight (DJ Mix)", "duration": "1:12:30"},
            {"type_": "track", "position": "C1", "title": "Dusk", "duration": "3:99"},
        ]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(data).encode())):
            selected = load_release("https://www.discogs.com/release/456")
        self.assertEqual([track.duration_seconds for track in selected.edition.tracks], [365, 4350, None])
