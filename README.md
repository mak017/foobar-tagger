# foobar-tagger

A review-before-apply metadata and artwork project with a FLAC review window and terminal preview.
See [PLAN.md](PLAN.md) for the roadmap and [DESIGN.md](DESIGN.md) for the metadata
proposal interface.

## Current implementation

`main.py` previews one local FLAC release. It lists selected and context-only
files, extracts folder clues, and flags differing album or album-artist tags.
After local grouping is confirmed, it can load a specific Discogs release URL,
show the tracklist, accept explicit mappings, and print a read-only proposal with
existing values, proposed changes, source evidence, and unresolved fields.

The pure `core.rules_engine.propose_metadata(context, policy)` function implements
credited metadata, release classification, vinyl/LP numbering, release-wide genres,
and release-wide dates. Vinyl supports continuous release numbering,
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

`MetadataProposal.evaluated_rules` identifies the rule families evaluated for the
given inputs. The artwork window previews covers and applies separately approved
folder or embedded artwork.

## Review and apply metadata (PowerShell)

From the repository root, with dependencies installed, open the Tkinter review
with an optional release folder or list of selected FLACs.

```powershell
.\.venv\Scripts\python main.py --review
.\.venv\Scripts\python main.py --review '.\Release' --accept-style Trance
```

Confirm the release grouping, then choose **Find editions** or load a known
Discogs release URL directly. Search starts with the folder catalog number when
available. Use the editable filters or switch to the artist and title clues to
broaden the search. Compare the candidate catalog, format, year, country, and
label. **Preview tracklist** shows Discogs positions, titles, and available
durations alongside the selected FLAC evidence. **Use selected edition** loads
the edition you choose, even when the results contain several editions with the
same catalog number. If Discogs requires authentication, supply a personal
token through `DISCOGS_TOKEN`. [Edition search screenshot](docs/edition-search.jpg)

After an edition is loaded, a question mark beside a position marks an
unconfirmed suggestion. Select that file to inspect its evidence and choose
**Use suggestion** or pick a track manually. Suggestions use the local title
tag or filename, the mix or version, and corroboration from duration or position.
Duration differences of at most five seconds can support a suggestion. Larger
differences, conflicting versions or positions, duplicate claims, and incomplete
tracklists are left for manual review. Changing editions clears the mappings and
approvals. [Track mapping screenshot](docs/track-mapping.jpg)

Mapped tracks receive Artist, Title, and Album proposals from the selected
Discogs edition. Artist credits preserve printed name variants and joins, and
track titles keep their mix or version subtitles. Album Artist uses the full
edition even when you select one file. Identical credits on every track propose
an explicit deletion. Differing known credits use the main release artist or
exactly `Various` for a various-artists edition. Missing track credits remain
unresolved when the source supplies separate track artists. [Artist guidelines](https://support.discogs.com/hc/en-us/articles/360005054753-Database-Guidelines-2-Artist) [Tracklisting guidelines](https://support.discogs.com/hc/en-us/articles/360005055373-Database-Guidelines-12-Tracklisting) [Credit review screenshot](docs/credited-metadata.jpg)

Release Type uses the selected edition's explicit Discogs format descriptions
and edition-wide DJ Mix credits when they establish one supported type. With no
explicit type, the proposal uses the full tracklist's count, duration, and artist
credits as fallback evidence. Conflicting or uncovered cases remain unresolved.
Use **Edit type** to select any of the 14 values in the plan or return to edition
evidence. The review shows the reason and source before approval.
[Discogs format guidelines](https://support.discogs.com/hc/en-us/articles/360005006654-Database-Guidelines-6-Format) [Release classification screenshot](docs/release-classification.jpg)

The matched edition also supplies label, catalog number, release country, and
media type. Joint labels and distinct catalog numbers are preserved in source
order. Discogs placeholders for absent labels or catalog numbers are never
written as tags. Missing details leave existing values in place.
[Discogs label and catalog guidelines](https://support.discogs.com/hc/en-us/articles/360005006614-Database-Guidelines-4-Label-Catalog-Number)

Release dates use the earliest supported Discogs edition when the two complete
tracklists agree on titles, versions, order, artist identities, and durations
within five seconds. An added or substituted track or a different explicit mix
uses the selected edition's date. The date proposal shows the source release.
Use **Edit date** to resolve uncertain cases, including overlapping dates for
early pressings, a missing master, and incomplete recording evidence.

Use **Edit styles** to update the release-wide genre value. The
existing and proposed values appear side by side, with source evidence and
unresolved issues below the table. CD and DVD track numbers restart on each
disc, and the total includes every track on that disc. Multi-disc editions
also receive disc numbers and the full disc count. WEB and vinyl track numbers
and totals use the full release. The review proposes removing existing disc tags
from single-disc CD/DVD, WEB, and vinyl files. Incomplete tracklists and ambiguous
CD/DVD positions leave the affected numbering fields unresolved.

Use the checkboxes or Space to approve individual changes. Linked date and
genre changes are selected together. When both disc tags change on a CD, DVD,
or WEB file, the two changes are approved together. Editing the inputs clears approvals.
**Apply approved** asks for confirmation, displays each field's outcome, and
refreshes the comparison. [Review window screenshot](docs/review-window.jpg)

## Review and apply artwork

Choose **Artwork** in the review window to inspect images in the release folder
and pictures embedded in selected FLACs. Load the Discogs edition to compare its
primary and secondary images, or open a local candidate as an unverified
alternative. Discogs credentials are optional. If needed, supply a personal token
through the `DISCOGS_TOKEN` environment variable.

The preview shows decoded dimensions, encoded bytes, and the prospective
`cover.png` or `cover.jpg` name. Candidates must be at least 1000 pixels on both
axes and strictly under 5,000,000 bytes after encoding. The converter preserves
aspect ratio and never upscales. Existing artwork stays unverified until its
edition identity is established.
[Artwork review screenshot](docs/artwork-review.jpg)

Once you confirm the release grouping, choose **Save folder cover**, **Embed in
selected FLACs**, or both. Review the exact paths and confirm that the replacement
matches your copy before applying. The folder output installs the verified cover
before removing reviewed alternate `cover`, `folder`, or `front` images. The
embedded output updates front covers while preserving other pictures and tags in
the selected FLACs. Oversized or unverified replacements can be skipped while you
continue with metadata review.

Each output is staged and verified before replacement. A failure in one output
can leave the other applied. The artwork window reports the outcome for each file
and flags any alternate cover that could not be removed.
[Artwork approval screenshot](docs/artwork-approval.jpg)

This application uses the Discogs API independently of Discogs, with no
affiliation, sponsorship, or endorsement. Discogs is a trademark of Zink Media,
LLC. [Discogs API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)

## Preview one release (PowerShell)

Use the environment setup below or install only the runtime dependencies with
`.\.venv\Scripts\python -m pip install -r requirements.txt`.

Launch with a release folder to select all its FLACs, or list just the FLACs you
want to review. The preview includes sibling FLACs in the release folder and its
immediate numbered disc subfolders for context. With file input, use
`--release-folder` to correct the inferred root if needed.

```powershell
.\.venv\Scripts\python main.py '.\Release'
.\.venv\Scripts\python main.py '.\Release\Track 01.flac'
```

Check the grouping and selection, then supply `--confirm-release` and the URL
for the specific Discogs edition. Run this step to see the edition's track
positions. To confirm a mapping, repeat the command with `--map` once per
selected file, using the relative path displayed in the preview.

```powershell
$releaseUrl = Read-Host 'Paste the URL of the Discogs edition'
.\.venv\Scripts\python main.py '.\Release\Track 01.flac' `
    --confirm-release --discogs-url $releaseUrl `
    --map 'Track 01.flac' A1 --accept-style Trance
```

Repeat `--accept-style` for each Discogs style accepted for this preview. Dates
stay unresolved when Discogs cannot establish the comparison. The CLI preview
does not modify any files.

## FLAC writer

`core.tag_writer.apply_metadata(local, proposal, approved)` takes the original
snapshot from `inspect_release`, the proposal shown for review, and the exact
`FieldDecision` records approved by the caller. Linked release-wide decisions
must be approved together. The writer stages changes beside each original,
reopens and verifies the FLAC, checks the original's file state again, then
replaces it. Results report individual changed, failed, and skipped fields and
flag release-wide inconsistencies after partial application.

The current writer uses FLAC Vorbis comments `ARTIST`, `TITLE`, `ALBUM`,
`ALBUMARTIST`, `RELEASETYPE`, `DATE`, `RELEASE DATE`, `GENRE`, `TRACKNUMBER`, `TOTALTRACKS`,
`DISCNUMBER`, `TOTALDISCS`, `VINYLSIDE`, `VINYLTRACKNUMBER`, `ORGANIZATION`,
`CATALOGNUMBER`, `RELEASECOUNTRY`, and `MEDIATYPE` for the implemented rules.
It recognizes `ALBUM ARTIST`, `TRACKTOTAL`, `DISCTOTAL`, and `LABEL`
as aliases and removes them when an approved change replaces or
deletes their logical field. The mappings have been round-tripped with Mutagen
on disposable FLACs. Checking them in foobar2000 remains pending.

After an interruption, the review window lists leftover temporary copies when
the release is reopened. The terminal preview remains read-only.

## Development (PowerShell)

Developed and tested with Python 3.13.7. The FLAC reader uses Mutagen and artwork
uses Pillow. The development requirements also install Pyright for static type
checking. Node.js must be available, or the Python wrapper may download it.

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
The Discogs loader looks for one earliest dated eligible release on the first
page of the master versions, sorted by release date. It skips entries marked as
promos, represses, reissues, test pressings, advances, or unofficial releases.
When another page may contain an equally early candidate, the result stays
unresolved. Callers can also supply normalized `DateSource(provider, release_id,
value)` facts and an evidenced `content_relationship`.

| Relationship | Date chosen |
| --- | --- |
| `original` | Selected edition's date. |
| `changed` | Selected edition's date: added/substituted recordings or new mixes make a distinct release. |
| `unchanged` | Supported original date: packaging, media changes, or remastering alone do not reset it. Missing original evidence stays unresolved. |
| `unknown` | Unresolved, unless supported edition/original dates agree exactly. Never infer content identity from an earliest master year. |

Date facts accept a valid four-digit year or complete calendar date. The Discogs
loader retains only the year from approximate dates such as `1998-02-00`.
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
