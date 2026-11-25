import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from core.flac_input import directory_identity, file_state, inspect_release
from core.tag_writer import StagingReview, cleanup_staging_files, review_staging_files
from tests.test_read_only_workflow import write_track


class StagingCleanupTests(unittest.TestCase):
    def test_only_unchanged_copies_from_exited_writers_are_removed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "Release"
            disc = root / "CD 1"
            disc.mkdir(parents=True)
            track = disc / "Signal.flac"
            write_track(track, "Trance")
            original = track.read_bytes()
            worker = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                                    capture_output=True, text=True, check=True)
            pid = int(worker.stdout.strip())
            abandoned = disc / f".foobar-tagger-{pid}-abcdefgh.flac.tmp"
            changed = root / f".foobar-tagger-{pid}-abcdefgh.cover.tmp"
            live = root / f".foobar-tagger-{os.getpid()}-ijklmnop.cover.tmp"
            legacy = root / ".foobar-tagger-abcdefgh.flac.tmp"
            other = root / "unrelated.tmp"
            outside = root.parent / f".foobar-tagger-{pid}-abcdefgh.cover.tmp"
            for path in (abandoned, changed, live, legacy, other, outside):
                path.write_bytes(b"temporary")
            local = inspect_release([root])

            review = review_staging_files(local)
            self.assertEqual({path for path, _, _ in review.removable}, {abandoned, changed})
            self.assertEqual(set(review.other), {live, legacy})
            changed.write_bytes(b"different content after review")
            forged = (outside, file_state(outside), directory_identity(outside.parent))
            result = cleanup_staging_files(local, StagingReview((*review.removable, forged), review.other))

            self.assertEqual({Path(item.file_id): item.status for item in result.fields},
                             {abandoned: "changed", changed: "skipped", outside: "skipped"})
            self.assertFalse(abandoned.exists())
            self.assertTrue(all(path.exists() for path in (changed, live, legacy, other, outside)))
            self.assertEqual(track.read_bytes(), original)
