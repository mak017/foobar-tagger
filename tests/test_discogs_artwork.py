from io import BytesIO
import json
import unittest
from unittest.mock import patch

from providers.discogs import DiscogsError, ReleaseImage, download_image, load_release


class DiscogsArtworkTests(unittest.TestCase):
    def test_associates_only_returned_discogs_images_with_the_confirmed_edition(self):
        payload = {"id": 123, "title": "Afterlight", "images": [
            {"type": "secondary", "uri": "https://i.discogs.com/image-back"},
            {"type": "primary", "uri": "https://i.discogs.com/image-front",
             "uri150": "https://i.discogs.com/thumbnail"},
            {"type": "secondary", "uri": "https://example.org/unrelated-image"},
            {"type": "secondary", "uri": "https://i.discogs.com/image-back"},
        ]}
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode())):
            release = load_release("https://www.discogs.com/release/123-Afterlight")

        self.assertEqual(release.edition.release_id, "123")
        self.assertEqual(release.images, (
            ReleaseImage("https://i.discogs.com/image-front", True),
            ReleaseImage("https://i.discogs.com/image-back", False),
        ))

    def test_fetches_the_actual_payload_and_refuses_unrelated_urls(self):
        with patch("urllib.request.OpenerDirector.open", return_value=BytesIO(b"encoded image")) as fetch:
            self.assertEqual(download_image(ReleaseImage("https://i.discogs.com/source", True)), b"encoded image")
            with self.assertRaisesRegex(DiscogsError, "supported image URL"):
                download_image(ReleaseImage("https://example.org/cover.jpg", True))

        self.assertEqual(fetch.call_count, 1)
