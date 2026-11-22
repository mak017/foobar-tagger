from collections.abc import Callable
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC, Picture

from core.flac_input import LocalRelease, inspect_release
from core.rules_engine import (
    Edition, EditionTrack, GenreSource, ManualDate, MetadataPolicy, MetadataProposal,
    ReleaseContext, propose_metadata,
)
from core.tag_writer import InvalidApproval, apply_metadata, find_staged_files
from tests.test_read_only_workflow import SILENT_FLAC, write_track


def review(paths: list[Path], *, genres: bool = False, date: str | None = None) -> tuple[LocalRelease, MetadataProposal]:
    local = inspect_release(paths, release_folder=paths[0].parent)
    edition = Edition("discogs", "123", "Vinyl", (
        EditionTrack("a", "A1"), EditionTrack("b", "B1"),
    ), True, genre_sources=(GenreSource("discogs", "123", ("Trance",)),) if genres else None)
    mapping = {path.name: track.track_id for path, track in zip(paths, edition.tracks, strict=False)}
    context = ReleaseContext(local.files, local.selected_file_ids, edition, mapping,
                             membership_complete=True, manual_date=ManualDate(date) if date else None)
    return local, propose_metadata(context, MetadataPolicy(accepted_styles=frozenset({"Trance"})))


class TagWriterTests(unittest.TestCase):
    def test_applies_only_approved_fields_and_preserves_unmanaged_flac_data(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected, sibling = root / "First.flac", root / "Second.flac"
            write_track(selected, "Trance")
            write_track(sibling, "Trance")
            audio = FLAC(str(selected))
            audio["TOTALDISCS"] = "2"
            audio["DISCTOTAL"] = "2"
            audio["X-KEEP"] = ["one", "two"]
            picture = Picture()
            picture.mime = "image/png"
            picture.data = b"embedded picture fixture"
            audio.add_picture(picture)
            audio.save()
            sibling_before = sibling.read_bytes()

            local, proposal = review([selected])
            approved = tuple(decision for decision in proposal.decisions
                             if decision.field in ("%TOTALTRACKS%", "%TOTALDISCS%"))

            result = apply_metadata(local, proposal, approved)

            written = FLAC(str(selected))
            self.assertEqual(written["TOTALTRACKS"], ["2"])
            self.assertNotIn("TOTALDISCS", written)
            self.assertNotIn("DISCTOTAL", written)
            self.assertNotIn("TRACKNUMBER", written)
            self.assertEqual(written["X-KEEP"], ["one", "two"])
            self.assertEqual(written.pictures[0].data, picture.data)
            self.assertTrue(selected.read_bytes().endswith(SILENT_FLAC[-11:]))
            self.assertEqual(sibling.read_bytes(), sibling_before)
            self.assertEqual(set(root.iterdir()), {selected, sibling})
            self.assertEqual({(field.file_id, field.field) for field in result.fields if field.status == "changed"}, {
                (selected.name, "%TOTALTRACKS%"), (selected.name, "%TOTALDISCS%"),
            })

    def test_refuses_to_replace_a_file_edited_during_staging(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "First.flac"
            write_track(path, "Trance")
            local, proposal = review([path])
            approved = tuple(decision for decision in proposal.decisions if decision.field == "%TRACKNUMBER%")
            sync = os.fsync

            def external_edit(fd: int) -> None:
                audio = FLAC(str(path))
                audio["COMMENT"] = "A change after review, just before replacement."
                audio.save()
                sync(fd)

            with patch("os.fsync", side_effect=external_edit):
                result = apply_metadata(local, proposal, approved)

            failed = [field for field in result.fields if field.status == "failed"]
            self.assertEqual([(field.file_id, field.field) for field in failed], [(path.name, "%TRACKNUMBER%")])
            self.assertIn("Refresh the review", failed[0].reason)
            self.assertEqual(FLAC(str(path))["COMMENT"], ["A change after review, just before replacement."])
            self.assertNotIn("TRACKNUMBER", FLAC(str(path)))
            self.assertEqual(find_staged_files(local), ())

    def test_failed_verification_preserves_the_original_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "First.flac"
            write_track(path, "Trance")
            local, proposal = review([path])
            approved = tuple(decision for decision in proposal.decisions if decision.field == "%TRACKNUMBER%")
            before = path.read_bytes()
            save = FLAC.save

            def change_unmanaged_tag(staged: FLAC, *, padding: Callable[[object], int]) -> None:
                staged["TITLE"] = "Unexpected title"
                save(staged, padding=padding)

            with patch.object(FLAC, "save", autospec=True, side_effect=change_unmanaged_tag):
                result = apply_metadata(local, proposal, approved)

            failed = [field for field in result.fields if field.status == "failed"]
            self.assertEqual([field.field for field in failed], ["%TRACKNUMBER%"])
            self.assertIn("did not verify", failed[0].reason)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(find_staged_files(local), ())

    def test_date_approval_requires_the_whole_group(self):
        with TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "First.flac", Path(tmp) / "Second.flac"
            for path in (first, second):
                write_track(path, "Trance")
            local, proposal = review([first, second], date="2020-06-12")
            dates = tuple(decision for decision in proposal.decisions if decision.field in ("%DATE%", "%RELEASE DATE%"))
            before = [path.read_bytes() for path in (first, second)]

            with self.assertRaisesRegex(InvalidApproval, "complete linked changes"):
                apply_metadata(local, proposal, dates[:1])
            self.assertEqual([path.read_bytes() for path in (first, second)], before)

            result = apply_metadata(local, proposal, dates)

            self.assertEqual(result.inconsistent_fields, ())
            for path in (first, second):
                written = FLAC(str(path))
                self.assertEqual(written["DATE"], ["2020"])
                self.assertEqual(written["RELEASE DATE"], ["2020-06-12"])

    def test_reports_partial_replacement_and_actual_genre_inconsistency(self):
        with TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "First.flac", Path(tmp) / "Second.flac"
            for path in (first, second):
                write_track(path, "House")
            local, proposal = review([first, second], genres=True)
            approved = tuple(decision for decision in proposal.decisions if decision.field == "%GENRE%")
            replace = os.replace

            def fail_second(source: Path, destination: Path) -> None:
                if destination == second:
                    raise PermissionError("File is locked")
                replace(source, destination)

            with patch("os.replace", side_effect=fail_second):
                result = apply_metadata(local, proposal, approved)

            self.assertEqual({field.file_id: field.status for field in result.fields if field.field == "%GENRE%"},
                             {first.name: "changed", second.name: "failed"})
            self.assertEqual(result.inconsistent_fields, ("%GENRE%",))
            self.assertEqual(FLAC(str(first))["GENRE"], ["Trance"])
            self.assertEqual(FLAC(str(second))["GENRE"], ["House"])
            self.assertEqual(find_staged_files(local), ())
