from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request

from mutagen.flac import FLAC
from PIL import Image

from core.cover_engine import FRONT_COVER_TYPE, prepare_cover
from core.cover_writer import apply_artwork, review_artwork
from core.flac_input import inspect_release
from core.rules_engine import MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import apply_metadata, find_staged_files
from main import main
from providers.discogs import load_release
from tests.test_read_only_workflow import write_track
from ui.diff_table import ReviewApproval


RELEASE_URL = "https://www.discogs.com/release/42"


def discogs_response(req: Request, *, timeout: int) -> BytesIO:
    if req.full_url.endswith("/releases/42"):
        payload = {
            "id": 42, "master_id": 7, "title": "Signal", "released": "2024-08-01", "country": "UK",
            "formats": [{"name": "File", "descriptions": ["Single"]}], "styles": ["Trance"],
            "labels": [{"name": "Signal Records", "catno": "SIG-42"}], "artists": [{"id": 1, "name": "Signal"}],
            "tracklist": [{"type_": "track", "position": "1", "title": "One"},
                          {"type_": "track", "position": "2", "title": "Two"}],
        }
    elif "/masters/7/versions?" in req.full_url:
        payload = {"pagination": {"page": 1, "pages": 1}, "versions": [{"id": 42, "released": "2024-08-01"}]}
    else:
        raise AssertionError(f"Unexpected Discogs request {req.full_url}")
    return BytesIO(json.dumps(payload).encode())


class FlacAcceptanceTests(unittest.TestCase):
    def test_direct_launch_full_edition_review_and_independent_artwork_application(self):
        policy = MetadataPolicy(frozenset({"Trance"}))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "One.flac", root / "Two.flac"
            for path in (first, second):
                write_track(path, "Techno", album="Signal")
            original = {path: path.read_bytes() for path in (first, second)}
            terminal = StringIO()
            with patch("urllib.request.urlopen", side_effect=discogs_response), redirect_stdout(terminal):
                self.assertEqual(main([str(first), "--confirm-release", "--discogs-url", RELEASE_URL,
                                       "--map", first.name, "1", "--accept-style", "Trance"]), 0)
                edition = load_release(RELEASE_URL).edition
            self.assertIn("DATE_SELECTION_CONFLICT", terminal.getvalue())
            self.assertIn("GENRE_SELECTION_CONFLICT", terminal.getvalue())
            self.assertEqual({path: path.read_bytes() for path in original}, original)

            single = inspect_release([first], root)
            proposal = propose_metadata(ReleaseContext(single.files, single.selected_file_ids, edition,
                                                       {first.name: "1"}, membership_complete=True), policy)
            approval = ReviewApproval(proposal)
            for field in ("%LABEL%", "%CATALOGNUMBER%", "%TRACKNUMBER%", "%TOTALTRACKS%"):
                approval.toggle((first.name, field))
            applied = apply_metadata(single, proposal, approval.selected())
            self.assertEqual(sum(item.status == "changed" for item in applied.fields), 4)
            self.assertEqual(second.read_bytes(), original[second])

            full = inspect_release([root])
            proposal = propose_metadata(ReleaseContext(full.files, full.selected_file_ids, edition,
                                                       {first.name: "1", second.name: "2"}, membership_complete=True), policy)
            approval = ReviewApproval(proposal)
            approval.toggle((first.name, "%DATE%"))
            approval.toggle((first.name, "%GENRE%"))
            applied = apply_metadata(full, proposal, approval.selected())
            self.assertEqual(sum(item.status == "changed" for item in applied.fields), 6)
            self.assertEqual(applied.inconsistent_fields, ())

            raw_cover = BytesIO()
            Image.new("RGB", (1000, 1100), "#264752").save(raw_cover, format="PNG")
            cover = prepare_cover(raw_cover.getvalue())
            single = inspect_release([first], root)
            before_art = {path: path.read_bytes() for path in (first, second)}
            folder_result = apply_artwork(review_artwork(single, cover), save_folder=True, embed=False)
            self.assertEqual([item.status for item in folder_result.fields], ["changed"])
            self.assertEqual({path: path.read_bytes() for path in before_art}, before_art)
            embed_result = apply_artwork(review_artwork(single, cover), save_folder=False, embed=True)
            self.assertEqual([item.status for item in embed_result.fields], ["changed"])

            self.assertEqual((root / cover.filename).read_bytes(), cover.data)
            self.assertEqual(second.read_bytes(), before_art[second])
            written = FLAC(str(first))
            self.assertEqual(written["ORGANIZATION"], ["Signal Records"])
            self.assertEqual(written["CATALOGNUMBER"], ["SIG-42"])
            self.assertEqual(written["TOTALTRACKS"], ["2"])
            self.assertEqual([(picture.type, picture.width, picture.height, picture.data) for picture in written.pictures],
                             [(FRONT_COVER_TYPE, 1000, 1100, cover.data)])
            for path in (first, second):
                audio = FLAC(str(path))
                self.assertEqual([audio[tag] for tag in ("DATE", "RELEASE DATE", "GENRE")],
                                 [["2024"], ["2024-08-01"], ["Trance"]])
            self.assertEqual(find_staged_files(inspect_release([root])), ())

    def test_permission_and_space_failures_leave_the_original_and_no_staging_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "One.flac"
            write_track(path, "Trance", album="Signal")
            with patch("urllib.request.urlopen", side_effect=discogs_response):
                edition = load_release(RELEASE_URL).edition
            local = inspect_release([root])
            proposal = propose_metadata(ReleaseContext(local.files, local.selected_file_ids, edition, {path.name: "1"}),
                                        MetadataPolicy())
            approved = tuple(d for d in proposal.decisions if d.field == "%TRACKNUMBER%")
            original = path.read_bytes()
            for target, value, reason in (("core.tag_writer.os.access", False, "Write permission"),
                                          ("core.tag_writer.shutil.disk_usage", SimpleNamespace(free=0), "Insufficient space")):
                with self.subTest(target=target), patch(target, return_value=value):
                    result = apply_metadata(local, proposal, approved)
                    (failure,) = (item for item in result.fields if item.status == "failed")
                    self.assertIn(reason, failure.reason)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(find_staged_files(local), ())
