# foobar-tagger

A planned review-before-apply metadata and artwork tool. See [PLAN.md](PLAN.md)
for behavior and [DESIGN.md](DESIGN.md) for the approved metadata proposal interface.

## Current implementation

The pure `core.rules_engine.propose_metadata(context, policy)` function implements
vinyl/LP numbering and release-wide genre proposals. Vinyl supports continuous release numbering,
full-release totals, separate side/within-side values, explicit disc-tag deletion,
unchanged values, partial selections, unresolved mappings/positions, incomplete
tracklists, and structural identity validation. Decisions include source evidence
and reasons. Input records defensively copy collections into immutable forms.

Genres use the matched Discogs styles in source order, deduplicated into one
comma-and-space-separated text value on all release tracks. Store evidence is
used only without Discogs styles, through explicit mappings; the default mapping
is `Melodic House & Techno` to `Progressive House`. Missing/unmapped/unaccepted
styles or conflicting sources stay unresolved and preserve existing tags.
Manual release-wide genre choices override provider evidence but still require
vocabulary validation and selection consistency. Genre changes are returned in
linked approval groups; later application orchestration must enforce them.

`MetadataProposal.evaluated_rules` explicitly identifies the implemented rule
families. A proposal is not write authorization or proof that other metadata rules
have passed. Dates, artist credits, classification, and non-vinyl numbering are
not implemented. There are no providers, audio writes, artwork
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

## Supplying genre evidence

`Edition.genre_sources=None` means genre inspection has not run; genre rules
are not evaluated. Use an empty tuple after inspection finds no evidence, so
the proposal reports unresolved genres rather than silently skipping them.
Each `GenreSource` must already be established as applicable to the selected
edition; this module does not search or verify provider matches.

For example, continue the Python session above:

```python
from dataclasses import replace
from core.rules_engine import GenreSource

genre_context = replace(
    context,
    membership_complete=True,
    edition=replace(context.edition, genre_sources=(
        GenreSource("discogs", "example", ("Trance", "Progressive Trance")),
    )),
)
policy = MetadataPolicy(accepted_styles=frozenset({"Trance", "Progressive Trance"}))
proposal = propose_metadata(genre_context, policy)
```

`membership_complete=True` asserts that `files` includes every local file in the
confirmed release, including unselected siblings. The default is false. Never set
it merely because every selected file has been inspected. A genre change is
withheld if inspection is incomplete, a selected mapping is unresolved, or an
unselected sibling has a different target genre. Expand selection or skip genres
in that case; recompute and review the proposal after changing inputs.

`ReleaseContext.manual_genres=("Techno",)` supplies a release-wide manual choice;
`None` means no override. An empty tuple does not request deletion. Clearing or
skipping genres through explicit manual commands is not implemented yet.

The accepted vocabulary defaults to empty, deliberately admitting no style until
configured. Add individual Discogs-style names explicitly. Vocabulary names and
mapping targets cannot be empty, padded, or comma-containing. A configured mapping
whose target is not accepted stays unresolved when used. Existing physical
multi-valued genres are compared with the required single text value and proposed
for conversion rather than treated as already compliant.
