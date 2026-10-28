# Metadata proposal module: interface proposal

Status: interface and first test seam approved. PLAN.md remains the behavior specification.
The vinyl-numbering and genre slices are implemented; the rest of this document describes
the intended module as later behaviors are added. See README.md for current scope.

## Decision

Expose one pure function from `core/rules_engine.py`:

```python
def propose_metadata(
    context: ReleaseContext,
    policy: MetadataPolicy,
) -> MetadataProposal:
    ...
```

The caller supplies inspected local metadata, a confirmed edition, explicit
track mappings, and any manual field decisions. The function returns a complete
reviewable proposal. It performs no network access, filesystem access, writes,
or UI operations and does not mutate its inputs. Equal inputs produce equal
results in stable file/field order.

This is a deep module: numbering, dates, genres, conditional artists, validation,
diff generation, and consistency constraints sit behind one interface. If it were
deleted, these rules would reappear in the UI and writer orchestration.

## Alternatives considered

| Interface | Consequence |
| --- | --- |
| Public functions for every normalization rule | Callers must know ordering, combine provenance, and enforce cross-field consistency themselves. |
| Stateful session object that searches, edits, and writes | Couples provider failures, UI state, and filesystem effects to metadata policy. |
| Pure proposal function (chosen) | Centralizes policy and validation while leaving searching, review interaction, and writes independently replaceable. |

Do not introduce a provider base class, rule plugin framework, or alternate
storage adapter merely for future possibilities. Add a seam when actual callers
or varying implementations justify it. Private helpers may evolve freely.

## Input contract

The following are conceptual immutable records. Their Python definitions should
grow with the implemented behaviors, rather than scaffolding every field now.

| Record | Required knowledge |
| --- | --- |
| `ReleaseContext` | Confirmed local release membership, local file snapshots, selected file IDs, confirmed edition, explicit file-to-track mapping, and manual field decisions. |
| `LocalFile` | Stable file ID, logical tag values, and mapping status. Paths are opaque identifiers here; the caller retains filesystem state for write preflight. |
| `Edition` | Provider/release identity, media type, full ordered playable tracklist, release credits, and evidence-backed metadata facts. |
| `EditionTrack` | Stable edition-scoped track ID, credited artists/title, source position, and any verified disc attribution. |
| `Evidence` | Source kind, provider/release identity when applicable, original value, and applicability to the selected edition. Manual evidence is explicitly labeled. |
| `MetadataPolicy` | Accepted Discogs-style vocabulary and explicit store-style mappings. Initially include the approved `Melodic House & Techno` to `Progressive House` mapping. |

Logical field keys use the exact spelling from PLAN.md, including `%GENRE%`,
`%ALBUM ARTIST%`, and `%RELEASE DATE%`. Existing tag values are sequences of text
values, so absent, empty, and multi-valued tags remain distinguishable. The
writer later translates logical values into physical format-specific fields.

Inputs must preserve uncertainty rather than collapse it into empty strings:

- Missing evidence, explicit absence, and conflicting evidence are different.
- Track mappings are confirmed or unresolved; do not guess inside this module.
- Provider tracklists contain playable tracks, with headings excluded by provider
  parsing. Preserve original position strings and provenance. Unknown structure
  remains unresolved rather than being normalized into a plausible position.
- Date evidence includes edition date, any supported original date, and an
  evidenced content relationship: unchanged, changed, or unknown. An earliest
  master year alone does not prove unchanged recordings/tracklist.
- Full-edition completeness is explicit. Incomplete tracklists cannot establish
  totals or that all artist credits are identical.
- Local membership inspection is marked complete or incomplete. Unknown sibling
  metadata cannot establish release-wide consistency.
- Manual decisions distinguish set, delete, and skip. They target a release-wide
  field or a mapped track field as appropriate. No per-track genre/date override
  may bypass release-wide consistency. Selection of changes for application is
  separate from these proposed values.

The module validates structural input first. Duplicate file/track IDs, unknown
selected IDs, mappings to nonexistent tracks, or mappings for nonexistent files
raise `InvalidProposalInput` with actionable details. Ordinary missing facts,
ambiguous matches, and invalid manual values return review issues instead of
raising. Invalid global policy configuration raises `InvalidMetadataPolicy`.

## Output contract

`MetadataProposal` contains ordered `FieldDecision` records and review issues.
Each selected file receives decisions for the fields evaluated by the implemented
rules. Unselected files may be referenced as consistency evidence but never
receive change operations.

Each decision identifies file, logical field, existing values, proposed values,
evidence, transformation reason, and one of these explicit states:

| State | Meaning |
| --- | --- |
| `SET` | Proposed addition/replacement with nonempty validated values. |
| `DELETE` | Proposed explicit removal; never an accidental consequence of missing data. |
| `KEEP` | No change, including an explicit skip or an already equal value. |
| `UNRESOLVED` | Evidence or validation is insufficient; preserve the existing value. |

Deletion has its own state; neither `None` nor an empty string means delete.
Unresolved decisions can retain a suggested candidate for display, but that
candidate is never an applicable operation. Issues carry stable reason codes,
human-readable explanations, affected fields/files, and actionable options such
as manual mapping, corrected value, selection expansion, or skipping a field.

The proposal also carries constraints between decisions:

- Release-wide date and genre changes are approval groups. The UI must not let
  independent field checkboxes create an inconsistent release.
- Paired disc fields and validated date/year combinations remain coherent.
- A proposed date/genre change conflicting with an unselected sibling is
  unresolved, naming the conflicting files and selection-expansion option.
- If local membership inspection is incomplete, mark consistency unverified and
  withhold affected release-wide changes until context is refreshed or skipped.

A proposal is never write authorization. Before dispatching any approved subset,
application orchestration must verify these constraints against the resulting
release state. Changing selection, edition, mapping, evidence, or manual values
requires recomputing the proposal and renewing review of affected changes.

## Rule ownership and ordering

The implementation owns dependency ordering; callers do not invoke individual
rules. Resolve verified facts and manual decisions, validate values, derive
release-wide/track-specific fields, compare with existing values, then evaluate
cross-field and selection constraints.

- Track-specific changes require confirmed mapping. For an unresolved selected
  track, leave that track unchanged and explain the missing mapping.
- Dates follow PLAN.md section 5. Unknown reissue content relationships remain
  unresolved when they would change the chosen date; do not invent an original
  date. Preserve complete-date versus year-only knowledge.
- Genres follow section 6, including one comma-separated text value and source
  precedence. A manual decision is still vocabulary-validated.
- Album artist uses full-edition credits and exactly `Various` where appropriate.
- Numbering follows section 7; unknown positions stay unresolved. Independent
  safe changes can still be proposed, including removal of forbidden vinyl disc
  tags on confirmed mapped tracks.
- Classification follows section 8; fallback clues do not override explicit
  classification or force uncovered cases.
- Provenance and explicit conflict reasons accompany every changed field.
- Unmanaged tags are not output as operations; preserving their physical values
  remains a writer obligation.

## Worked first slice

Confirmed vinyl edition, complete playable tracklist `A1, A2, B1, C1, D1`;
five local files selected and explicitly mapped. Each currently has disc number
`1` and total discs `2`; numbering fields are absent.

| Position | `%TRACKNUMBER%` | `%TOTALTRACKS%` | `%VINYLSIDE%` | `%VINYLTRACKNUMBER%` |
| --- | --- | --- | --- | --- |
| `A1` | `1` | `5` | `A` | `1` |
| `A2` | `2` | `5` | `A` | `2` |
| `B1` | `3` | `5` | `B` | `1` |
| `C1` | `4` | `5` | `C` | `1` |
| `D1` | `5` | `5` | `D` | `1` |

Return `SET` decisions containing one text value for each numeric/side field,
plus explicit `DELETE` decisions for both disc fields on all five files. Every
decision links back to the edition and explains the numbering rule. No file is
written. Other unimplemented rule families are outside the first slice; the
initial result must not claim a complete milestone-wide validation pass.

Subsequent slices cover side-only `A, B` (no vinyl track-number tags), an already
correct file (no spurious changes), partial selections (full edition totals,
no unselected operations), and unresolved mappings. An existing within-side
number on a confirmed side-only position requires an explicit deletion proposal.

## Proposed test seam for confirmation

| Public seam | Catches | Does not catch |
| --- | --- | --- |
| `propose_metadata(context, policy)` | Observable field values, explicit deletions, provenance, unresolved cases, validation, and selection/consistency constraints. | Provider parsing/search accuracy, filesystem races, physical tags, image quality, UI approval enforcement, or write safety. |

Begin TDD at this one seam using the worked vinyl example. Use synthetic immutable
records and independently specified expected values; do not mock private helpers
or test their call order. Add one failing behavioral test, implement enough to
pass, and repeat. Do not write all tests or all planned record fields up front.

Later work must separately agree tests at the provider parsing, approved-changeset
application, artwork, and writer seams when their interfaces are concrete. The
writer requires disposable FLAC round-trip tests; proposal tests cannot substitute
for them.

## Approved test scope

The user approved the pure `propose_metadata` interface and the vinyl example
as the first tested behavior. This approval covers the first module's seam and
initial behavior, not new provider behavior, a full implementation, or writes
to a personal library.
