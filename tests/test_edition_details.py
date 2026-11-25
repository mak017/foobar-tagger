from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mutagen.flac import FLAC

from core.flac_input import inspect_release
from core.rules_engine import LocalFile, MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import apply_metadata
from providers.discogs import load_release
from tests.test_read_only_workflow import write_track


FIELDS = {"%LABEL%", "%CATALOGNUMBER%", "%RELEASECOUNTRY%", "%MEDIATYPE%"}


def load_fixture(**details: object):
    payload = {"id": 12, "title": "Signal", "tracklist": [{"type_": "track", "position": "1", "title": "Signal"}],
               **details}
    with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(payload).encode())):
        return load_release("https://www.discogs.com/release/12").edition


class EditionDetailTests(unittest.TestCase):
    def test_edition_tags_round_trip_and_only_change_the_mapped_selection(self):
        edition = load_fixture(labels=[{"name": "Signal Records", "catno": "SG-01"},
                                       {"name": "Second Imprint", "catno": "Blue 02"},
                                       {"name": "Signal Records", "catno": "SG-01"}],
                               country="UK & Europe", formats=[{"name": "Vinyl", "descriptions": ["LP"]}])
        with TemporaryDirectory() as folder:
            root = Path(folder)
            selected, unselected = root / "Signal.flac", root / "Context.flac"
            for path in (selected, unselected):
                write_track(path, "Trance")
            flac = FLAC(str(selected))
            flac["LABEL"] = "Stale Label"
            flac["X-KEEP"] = "Keep"
            flac.save()
            original = unselected.read_bytes()
            local = inspect_release([selected], root)
            proposal = propose_metadata(ReleaseContext(local.files, local.selected_file_ids, edition, {selected.name: "1"}),
                                        MetadataPolicy())
            approved = tuple(d for d in proposal.decisions if d.field in FIELDS)
            self.assertEqual({d.evidence.release_id for d in approved}, {"12"})
            self.assertEqual({d.existing_values for d in approved if d.field == "%LABEL%"}, {("Stale Label",)})
            result = apply_metadata(local, proposal, approved)

            self.assertEqual({r.field for r in result.fields if r.status == "changed"}, FIELDS)
            written = FLAC(str(selected))
            self.assertEqual(written["ORGANIZATION"], ["Signal Records", "Second Imprint"])
            self.assertEqual(written["CATALOGNUMBER"], ["SG-01", "Blue 02"])
            self.assertEqual(written["RELEASECOUNTRY"], ["UK & Europe"])
            self.assertEqual(written["MEDIATYPE"], ["LP"])
            self.assertEqual(written["X-KEEP"], ["Keep"])
            self.assertNotIn("LABEL", written)
            self.assertEqual(unselected.read_bytes(), original)

    def test_missing_mixed_and_unmapped_evidence_preserve_existing_tags(self):
        edition = load_fixture(labels=[{"name": "Not On Label (Signal Self-released)", "catno": "none"}],
                               country="Unknown", formats=[{"name": "CD"}, {"name": "DVD"}])
        old = ("Existing",)
        local = LocalFile("existing.flac", dict.fromkeys(FIELDS, old))
        blank = LocalFile("blank.flac", {})
        proposal = propose_metadata(ReleaseContext((local, blank), frozenset({local.file_id, blank.file_id}), edition,
                                                  {local.file_id: "1"}), MetadataPolicy())

        self.assertEqual({(d.file_id, d.field, d.state, d.proposed_values) for d in proposal.decisions if d.field in FIELDS},
                         {(file.file_id, field, "UNRESOLVED", None) for file in (local, blank) for field in FIELDS})
