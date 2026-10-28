"""Pure metadata proposals; currently implements vinyl numbering only."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import re


class InvalidProposalInput(ValueError):
    """The context contains inconsistent file or edition identities."""


@dataclass(frozen=True)
class LocalFile:
    file_id: str
    tags: Mapping[str, tuple[str, ...]]

    def __post_init__(self):
        object.__setattr__(self, "tags", MappingProxyType({
            field: tuple(values) for field, values in self.tags.items()
        }))


@dataclass(frozen=True)
class EditionTrack:
    track_id: str
    position: str


@dataclass(frozen=True)
class Edition:
    provider: str
    release_id: str
    media_type: str
    tracks: tuple[EditionTrack, ...]
    tracklist_complete: bool

    def __post_init__(self):
        object.__setattr__(self, "tracks", tuple(self.tracks))


@dataclass(frozen=True)
class ReleaseContext:
    files: tuple[LocalFile, ...]
    selected_file_ids: frozenset[str]
    edition: Edition
    track_mapping: Mapping[str, str]

    def __post_init__(self):
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "selected_file_ids", frozenset(self.selected_file_ids))
        object.__setattr__(self, "track_mapping", MappingProxyType(dict(self.track_mapping)))


@dataclass(frozen=True)
class MetadataPolicy:
    """Policy fields will be added alongside the rules that consume them."""


@dataclass(frozen=True)
class Evidence:
    provider: str
    release_id: str
    original_value: str | None


@dataclass(frozen=True)
class FieldDecision:
    file_id: str
    field: str
    existing_values: tuple[str, ...] | None
    proposed_values: tuple[str, ...] | None
    state: str
    evidence: Evidence
    reason: str


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    message: str
    file_ids: tuple[str, ...]
    fields: tuple[str, ...]
    options: tuple[str, ...]


@dataclass(frozen=True)
class MetadataProposal:
    decisions: tuple[FieldDecision, ...]
    issues: tuple[ReviewIssue, ...]
    evaluated_rules: tuple[str, ...] = ("vinyl_numbering",)


def propose_metadata(context: ReleaseContext, policy: MetadataPolicy) -> MetadataProposal:
    """Return review decisions without reading or writing files."""
    edition = context.edition
    file_ids = [file.file_id for file in context.files]
    track_ids = [track.track_id for track in edition.tracks]
    if len(set(file_ids)) != len(file_ids):
        raise InvalidProposalInput("Duplicate file ID; each local file must have a unique ID.")
    if len(set(track_ids)) != len(track_ids):
        raise InvalidProposalInput("Duplicate track ID; each edition track must have a unique ID.")
    unknown = context.selected_file_ids - set(file_ids)
    if unknown:
        raise InvalidProposalInput(f"Unknown selected file IDs: {sorted(unknown)}")
    unknown = set(context.track_mapping) - set(file_ids)
    if unknown:
        raise InvalidProposalInput(f"Unknown mapped file IDs: {sorted(unknown)}")
    unknown = set(context.track_mapping.values()) - set(track_ids)
    if unknown:
        raise InvalidProposalInput(f"Unknown mapped track IDs: {sorted(unknown)}")
    decisions = []
    issues = []
    if edition.media_type not in ("Vinyl", "LP"):
        return MetadataProposal((), ())
    tracks = {track.track_id: (i, track) for i, track in enumerate(edition.tracks, 1)}
    for file in context.files:
        if file.file_id not in context.selected_file_ids:
            continue
        if file.file_id not in context.track_mapping:
            fields = ("%TRACKNUMBER%", "%TOTALTRACKS%", "%VINYLSIDE%",
                      "%VINYLTRACKNUMBER%", "%DISCNUMBER%", "%TOTALDISCS%")
            reason = "Confirm a provider track mapping before changing this file."
            issues.append(ReviewIssue("MAPPING_REQUIRED", reason, (file.file_id,),
                                      fields, ("manual_mapping", "skip")))
            for field in fields:
                decisions.append(FieldDecision(
                    file.file_id, field, file.tags.get(field), None, "UNRESOLVED",
                    Evidence(edition.provider, edition.release_id, None), reason,
                ))
            continue
        number, track = tracks[context.track_mapping[file.file_id]]
        position_match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)?", track.position)
        side, within_side = position_match.groups() if position_match else (None, None)
        evidence = Evidence(edition.provider, edition.release_id, track.position)
        targets = (
            ("%TRACKNUMBER%", (str(number),)),
            ("%TOTALTRACKS%", (str(len(edition.tracks)),)),
            ("%VINYLSIDE%", (side,) if side else None),
            ("%VINYLTRACKNUMBER%", (within_side,) if within_side else None),
            ("%DISCNUMBER%", None),
            ("%TOTALDISCS%", None),
        )
        for field, value in targets:
            existing = file.tags.get(field)
            if not position_match and field in ("%VINYLSIDE%", "%VINYLTRACKNUMBER%"):
                reason = "The source position does not establish a vinyl side or within-side number."
                decisions.append(FieldDecision(
                    file.file_id, field, existing, None, "UNRESOLVED", evidence, reason,
                ))
                continue
            if not edition.tracklist_complete and field in ("%TRACKNUMBER%", "%TOTALTRACKS%"):
                reason = "A complete edition tracklist is required for release numbering and totals."
                decisions.append(FieldDecision(
                    file.file_id, field, existing, None, "UNRESOLVED", evidence, reason,
                ))
                continue
            decisions.append(FieldDecision(
                file.file_id, field, existing, value,
                "KEEP" if existing == value else "DELETE" if value is None else "SET", evidence,
                "Vinyl uses continuous release numbering, separate side positions, and no disc tags.",
            ))
        if not edition.tracklist_complete:
            issues.append(ReviewIssue(
                "INCOMPLETE_TRACKLIST", "Retrieve or confirm the complete edition tracklist.",
                (file.file_id,), ("%TRACKNUMBER%", "%TOTALTRACKS%"),
                ("complete_tracklist", "skip"),
            ))
        if not position_match:
            issues.append(ReviewIssue(
                "VINYL_POSITION_REQUIRED", "Confirm the vinyl position manually.",
                (file.file_id,), ("%VINYLSIDE%", "%VINYLTRACKNUMBER%"),
                ("correct_position", "skip"),
            ))
    return MetadataProposal(tuple(decisions), tuple(issues))
