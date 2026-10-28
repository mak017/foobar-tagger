# foobar-tagger

A planned review-before-apply metadata and artwork tool. See [PLAN.md](PLAN.md)
for behavior and [DESIGN.md](DESIGN.md) for the approved metadata proposal interface.

## Current implementation

The pure `core.rules_engine.propose_metadata(context, policy)` function implements
vinyl/LP numbering proposals only. It supports continuous release numbering,
full-release totals, separate side/within-side values, explicit disc-tag deletion,
unchanged values, partial selections, unresolved mappings/positions, incomplete
tracklists, and structural identity validation. Decisions include source evidence
and reasons. Input records defensively copy collections into immutable forms.

`MetadataProposal.evaluated_rules` explicitly identifies the implemented rule
family. A proposal is not write authorization or proof that other metadata rules
have passed. `MetadataPolicy` currently has no fields; genre configuration will
be added with genre behavior. There are no providers, audio writes, artwork
processing, CLI application, or review UI yet.

## Development (PowerShell)

Developed and tested with Python 3.13.7. The current implementation and tests use
only the Python standard library; no dependency installation is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

Activation is optional; run tests directly with:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

## Run the current proposal example

Launch Python from the repository root:

```powershell
.\.venv\Scripts\python
```

Then enter:

```python
from core.rules_engine import (
    Edition, EditionTrack, LocalFile, MetadataPolicy, ReleaseContext,
    propose_metadata,
)

context = ReleaseContext(
    files=(LocalFile("file-a", {"%DISCNUMBER%": ("1",)}),),
    selected_file_ids=frozenset({"file-a"}),
    edition=Edition(
        provider="discogs",
        release_id="example",
        media_type="Vinyl",
        tracks=(EditionTrack("track-a", "A1"), EditionTrack("track-b", "B1")),
        tracklist_complete=True,
    ),
    track_mapping={"file-a": "track-a"},
)
proposal = propose_metadata(context, MetadataPolicy())
for decision in proposal.decisions:
    print(decision.field, decision.state, decision.proposed_values)
```

The synthetic example proposes track `1` of `2`, side `A`, within-side track `1`,
and removal of the existing disc number. It does not inspect or modify any file.
