# foobar-tagger

A planned review-before-apply metadata and artwork tool. See [PLAN.md](PLAN.md)
for behavior and [DESIGN.md](DESIGN.md) for the approved metadata proposal interface.

## Current implementation

The pure `core.rules_engine.propose_metadata(context, policy)` function implements
vinyl/LP numbering, release-wide genres, and release-wide dates. Vinyl supports continuous release numbering,
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
have passed. Artist credits, classification, and non-vinyl numbering are
not implemented. There are no providers, audio writes, artwork
processing, CLI application, or review UI yet.

## Development (PowerShell)

Developed and tested with Python 3.13.7. Runtime code and tests use only the Python
standard library. Install the pinned Pyright development dependency for static
type checking (Node.js must be available, or the Python wrapper may download it).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python scripts/typecheck.py
python -m unittest discover -s tests -v
```

Activation is optional; run tests directly with:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Run type checking after each Python code change:

```powershell
.\.venv\Scripts\python scripts/typecheck.py
```

For automatic rechecking while editing, keep this command running in a terminal:

```powershell
.\.venv\Scripts\python scripts/typecheck.py --watch
```

The runner works from any working directory, uses its active Python interpreter,
and propagates checker failures. Missing Pyright is a failed check with installation
instructions. Install it once before using the runner; the first invocation of
the Python wrapper may also need network access to obtain the checker.

`pyrightconfig.json` covers project Python files, including tests and scripts,
using standard diagnostics. It is also read by Pylance. Select the repository's
`.venv` interpreter in VS Code for consistent import resolution. See the official
[Pyright configuration documentation](https://github.com/microsoft/pyright/blob/main/docs/configuration.md).

The recurring check requirement lives in `AGENTS.md`, where it applies to every
Python change in this repository, including work using Pocock's skills.

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

## Supplying release dates

`Edition.dates=None` means dates have not been inspected and skips their rules.
`ReleaseDates()` explicitly represents inspected but unresolved date evidence.
Supply normalized `DateSource(provider, release_id, value)` facts and an evidenced
`content_relationship`:

| Relationship | Date chosen |
| --- | --- |
| `original` | Selected edition's date. |
| `changed` | Selected edition's date: added/substituted recordings or new mixes make a distinct release. |
| `unchanged` | Supported original date: packaging, media changes, or remastering alone do not reset it. Missing original evidence stays unresolved. |
| `unknown` | Unresolved, unless supported edition/original dates agree exactly. Never infer content identity from an earliest master year. |

Date facts accept a valid four-digit year or complete calendar date. Partial
`YYYY-MM`, zero dates, impossible dates, and malformed values stay unresolved.
If a source establishes only a year, represent that fact as `YYYY`; never insert
a guessed day/month. `%DATE%` receives the year and `%RELEASE DATE%` receives only
a complete date. The same values apply to every release track.

```python
from core.rules_engine import DateSource, ReleaseDates

dated_context = replace(
    context,
    membership_complete=True,
    edition=replace(context.edition, dates=ReleaseDates(
        edition_date=DateSource("discogs", "example", "2020-06-12"),
        original_date=DateSource("discogs", "original", "1998-02-03"),
        content_relationship="changed",
    )),
)
proposal = propose_metadata(dated_context, MetadataPolicy())
```

This example proposes 2020 for every track, not 1998. Matching providers and
establishing the content relationship remain caller obligations; no network
research happens inside the proposal module.

Year-only evidence leaves an absent exact date absent. If any local release file
already contains an exact-date field, including an invalid partial value, the
date pair is conservatively withheld for review: the module neither deletes it
nor changes the year around disputed evidence. Correct it with an explicit
release-wide manual date:

```python
from core.rules_engine import ManualDate

corrected = replace(dated_context, manual_date=ManualDate("2020-07-01"))
year_only = replace(dated_context, manual_date=ManualDate("2020", clear_exact_date=True))
```

Manual values override provider evidence but still require valid dates, complete
local membership inspection, confirmed selected mappings, and consistency with
unselected siblings. `clear_exact_date=True` requires a year-only value; existing
exact-date fields receive explicit `DELETE` proposals. A complete date together
with a deletion request is contradictory and stays unresolved.

Date changes return one approval group with `linked_changes` containing the exact
file/field/target tuples, including deletions. The UI and future application
orchestration must enforce this group so year and exact date cannot be approved
inconsistently. Unselected files never receive operations. Missing evidence,
unknown relationships, and conflicts preserve existing values and provide review
issues. Already correct values produce `KEEP` decisions without spurious changes.
