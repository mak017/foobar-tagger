import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC, Picture

from core.cover_engine import inspect_artwork, prepare_cover
from core.cover_writer import apply_artwork, review_artwork
from core.flac_input import inspect_release
from core.tag_writer import find_staged_files
from tests.test_cover_engine import image_bytes
from tests.test_read_only_workflow import SILENT_FLAC, write_track


class CoverWriterTests(unittest.TestCase):
    def test_finds_folder_staging_when_flacs_are_in_disc_subfolders(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            disc = root / "Disc 1"
            disc.mkdir()
            write_track(disc / "A.flac", "Trance")
            staging = root / ".foobar-tagger-interrupted.cover.tmp"
            staging.write_bytes(b"interrupted stage")

            self.assertEqual(find_staged_files(inspect_release([root])), (staging.resolve(),))

    def test_folder_write_removes_only_reviewed_cover_names_and_preserves_audio(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            track, old, alternate, booklet = (root / name for name in ("A.flac", "cover.jpg", "front.jpeg", "booklet.png"))
            write_track(track, "Trance")
            for path in (old, alternate, booklet):
                path.write_bytes(b"existing image")
            before = track.read_bytes()
            cover = prepare_cover(image_bytes("PNG", (1000, 1100)))
            review = review_artwork(inspect_release([root]), cover)

            result = apply_artwork(review, save_folder=True, embed=False)

            destination = root / "cover.png"
            self.assertEqual(destination.read_bytes(), cover.data)
            self.assertTrue(inspect_artwork(destination.read_bytes()).meets_output_requirements)
            self.assertEqual({path.name for path in root.iterdir()}, {track.name, booklet.name, destination.name})
            self.assertEqual(track.read_bytes(), before)
            self.assertEqual({Path(field.file_id).name: field.status for field in result.fields},
                             {destination.name: "changed", old.name: "changed", alternate.name: "changed"})

    def test_embedding_changes_only_selected_front_covers_and_keeps_other_metadata(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, sibling = root / "A.flac", root / "B.flac"
            source = image_bytes("JPEG", (200, 200))
            for path in (selected, sibling):
                write_track(path, "Trance")
                audio = FLAC(str(path))
                audio["X-KEEP"] = ["one", "two"]
                for picture_type in (3, 4):
                    picture = Picture()
                    picture.type, picture.mime, picture.data = picture_type, "image/jpeg", source
                    audio.add_picture(picture)
                audio.save()
            before = sibling.read_bytes()
            cover = prepare_cover(image_bytes("PNG", (1000, 1100)))
            review = review_artwork(inspect_release([selected], release_folder=root), cover)

            result = apply_artwork(review, save_folder=False, embed=True)

            audio = FLAC(str(selected))
            self.assertEqual([(picture.type, picture.mime, picture.data) for picture in audio.pictures], [
                (4, "image/jpeg", source), (3, "image/png", cover.data),
            ])
            self.assertEqual(audio["X-KEEP"], ["one", "two"])
            self.assertTrue(selected.read_bytes().endswith(SILENT_FLAC[-11:]))
            self.assertEqual(sibling.read_bytes(), before)
            self.assertEqual({path.name for path in root.iterdir()}, {selected.name, sibling.name})
            self.assertEqual([(field.file_id, field.status) for field in result.fields], [(selected.name, "changed")])

    def test_stale_folder_approval_leaves_old_covers_and_independent_embedding_succeeds(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, alternate = root / "A.flac", root / "folder.jpg"
            write_track(selected, "Trance")
            alternate.write_bytes(b"original")
            cover = prepare_cover(image_bytes("PNG", (1000, 1000)))
            review = review_artwork(inspect_release([root]), cover)
            alternate.write_bytes(b"edited by another application")

            result = apply_artwork(review, save_folder=True, embed=True)

            self.assertEqual([field.status for field in result.fields], ["failed", "skipped", "changed"])
            self.assertEqual(alternate.read_bytes(), b"edited by another application")
            self.assertFalse((root / "cover.png").exists())
            self.assertEqual(FLAC(str(selected)).pictures[0].data, cover.data)

    def test_folder_install_failure_keeps_the_alternate_and_removes_staging(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, alternate = root / "A.flac", root / "cover.jpg"
            write_track(selected, "Trance")
            alternate.write_bytes(b"original")
            cover = prepare_cover(image_bytes("PNG", (1000, 1000)))
            review = review_artwork(inspect_release([root]), cover)
            with patch.object(os, "replace", side_effect=PermissionError("target is locked")):
                result = apply_artwork(review, save_folder=True, embed=False)

            self.assertEqual([field.status for field in result.fields], ["failed", "skipped"])
            self.assertEqual(alternate.read_bytes(), b"original")
            self.assertEqual(find_staged_files(review.local), ())

    def test_reports_a_remaining_alternate_if_it_cannot_be_removed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, alternate = root / "A.flac", root / "cover.jpg"
            write_track(selected, "Trance")
            alternate.write_bytes(b"original")
            cover = prepare_cover(image_bytes("PNG", (1000, 1000)))
            review = review_artwork(inspect_release([root]), cover)
            unlink = Path.unlink

            def fail_alternate(path: Path, missing_ok: bool = False) -> None:
                if path == alternate:
                    raise PermissionError("alternate is locked")
                unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", autospec=True, side_effect=fail_alternate):
                result = apply_artwork(review, save_folder=True, embed=False)

            self.assertEqual([field.status for field in result.fields], ["changed", "failed"])
            self.assertIn(str(alternate.resolve()), result.warnings[0])
            self.assertEqual((root / "cover.png").read_bytes(), cover.data)
            self.assertEqual(alternate.read_bytes(), b"original")
            self.assertEqual(find_staged_files(review.local), ())

    def test_stale_flac_is_preserved_when_folder_output_succeeds(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected = root / "A.flac"
            write_track(selected, "Trance")
            cover = prepare_cover(image_bytes("JPEG", (1000, 1000)))
            review = review_artwork(inspect_release([root]), cover)
            audio = FLAC(str(selected))
            audio["COMMENT"] = "Updated during review"
            audio.save()
            before = selected.read_bytes()

            result = apply_artwork(review, save_folder=True, embed=True)

            self.assertEqual([field.status for field in result.fields], ["changed", "failed"])
            self.assertEqual((root / "cover.jpg").read_bytes(), cover.data)
            self.assertEqual(selected.read_bytes(), before)
            self.assertEqual(find_staged_files(review.local), ())
