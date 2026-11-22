import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC

from main import main


# One silent, 4096-sample mono FLAC frame at 44.1 kHz.
SILENT_FLAC = base64.b64decode(
    "ZkxhQ4AAACIQABAAAAALAAALCsRA8AAAEAAIKfcXQKqxq5izPq4h3uEi//jJCACVAAAAIb0="
)


def write_track(path: Path, genre: str, *, album: str = "Someday") -> None:
    path.write_bytes(SILENT_FLAC)
    audio = FLAC(str(path))
    audio["ALBUM"] = album
    audio["TITLE"] = path.stem
    audio["GENRE"] = genre
    audio.save(str(path))


def discogs_response() -> io.BytesIO:
    return io.BytesIO(json.dumps({
        "id": 123,
        "title": "Someday",
        "formats": [{"name": "Vinyl", "descriptions": ["12\""]}],
        "styles": ["Trance"],
        "tracklist": [
            {"type_": "heading", "position": "", "title": "Side A"},
            {"type_": "track", "position": "A1", "title": "Someday (Original Mix)"},
            {"type_": "track", "position": "B1", "title": "Someday (Remix)"},
        ],
    }).encode("utf-8"))


class ReadOnlyWorkflowTests(unittest.TestCase):
    def test_folder_preview_shows_selection_and_conflicting_albums_before_lookup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "[FSM005] Jason van Wyk & Audien - Someday (2011) WEB"
            root.mkdir()
            write_track(root / "Someday.flac", "Trance")
            write_track(root / "Later.flac", "Trance", album="Different Release")
            out = io.StringIO()

            with patch("urllib.request.urlopen") as fetch, redirect_stdout(out):
                status = main([str(root), "--discogs-url", "https://www.discogs.com/release/123"])

            self.assertEqual(status, 0)
            fetch.assert_not_called()
            output = out.getvalue()
            for value in (
                "Catalog: FSM005", "Artist: Jason van Wyk & Audien", "Album: Someday",
                "Year: 2011", "Media: WEB", "Selected: Later.flac", "Selected: Someday.flac",
                "Possible unrelated files", "title: Someday",
            ):
                self.assertIn(value, output)

    def test_selected_flac_uses_full_edition_and_checks_unselected_sibling(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "[FSM005] Jason van Wyk & Audien - Someday (2011) Vinyl"
            root.mkdir()
            selected = root / "Someday.flac"
            disc = root / "Disc 2"
            disc.mkdir()
            sibling = disc / "Remix.flac"
            write_track(selected, "House")
            write_track(sibling, "Techno")
            before = {path: path.read_bytes() for path in (selected, sibling)}
            out, err = io.StringIO(), io.StringIO()
            with (
                patch("urllib.request.urlopen", return_value=discogs_response()) as fetch,
                redirect_stdout(out), redirect_stderr(err),
            ):
                status = main([
                    str(selected), "--confirm-release",
                    "--discogs-url", "https://www.discogs.com/release/123-Someday",
                    "--map", "Someday.flac", "A1", "--accept-style", "Trance",
                ])

            self.assertEqual(status, 0, err.getvalue())
            self.assertEqual(fetch.call_args.args[0].full_url, "https://api.discogs.com/releases/123")
            output = out.getvalue()
            self.assertIn("Catalog: FSM005", output)
            self.assertIn("Context only: Disc 2/Remix.flac", output)
            self.assertIn("Someday (Original Mix)", output)
            self.assertIn("%TOTALTRACKS% SET: (absent) -> 2", output)
            self.assertIn("%GENRE% UNRESOLVED: House", output)
            self.assertIn("GENRE_SELECTION_CONFLICT", output)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
