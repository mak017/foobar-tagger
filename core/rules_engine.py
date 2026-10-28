"""Pure metadata proposals for implemented normalization rules."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
import re


class InvalidProposalInput(ValueError):
    """The context contains inconsistent file or edition identities."""


class InvalidMetadataPolicy(ValueError):
    """The style vocabulary or mapping configuration is malformed."""


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
class GenreSource:
    """Genre evidence already established as applicable to the selected edition."""

    provider: str
    release_id: str
    styles: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "styles", tuple(self.styles))


@dataclass(frozen=True)
class Edition:
    provider: str
    release_id: str
    media_type: str
    tracks: tuple[EditionTrack, ...]
    tracklist_complete: bool
    genre_sources: tuple[GenreSource, ...] | None = None

    def __post_init__(self):
        object.__setattr__(self, "tracks", tuple(self.tracks))
        if self.genre_sources is not None:
            object.__setattr__(self, "genre_sources", tuple(self.genre_sources))


@dataclass(frozen=True)
class ReleaseContext:
    files: tuple[LocalFile, ...]
    selected_file_ids: frozenset[str]
    edition: Edition
    track_mapping: Mapping[str, str]
    membership_complete: bool = False
    manual_genres: tuple[str, ...] | None = None

    def __post_init__(self):
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "selected_file_ids", frozenset(self.selected_file_ids))
        object.__setattr__(self, "track_mapping", MappingProxyType(dict(self.track_mapping)))
        if self.manual_genres is not None:
            object.__setattr__(self, "manual_genres", tuple(self.manual_genres))


@dataclass(frozen=True)
class MetadataPolicy:
    accepted_styles: frozenset[str] = frozenset()
    store_style_mappings: Mapping[str, str] = field(default_factory=lambda: {
        "Melodic House & Techno": "Progressive House",
    })

    def __post_init__(self):
        object.__setattr__(self, "accepted_styles", frozenset(self.accepted_styles))
        object.__setattr__(self, "store_style_mappings", MappingProxyType(dict(self.store_style_mappings)))


@dataclass(frozen=True)
class Evidence:
    provider: str
    release_id: str
    original_value: str | tuple[str, ...] | None


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
class ApprovalGroup:
    field: str
    file_ids: tuple[str, ...]
    target_values: tuple[str, ...]


@dataclass(frozen=True)
class MetadataProposal:
    decisions: tuple[FieldDecision, ...]
    issues: tuple[ReviewIssue, ...]
    evaluated_rules: tuple[str, ...] = ("vinyl_numbering",)
    approval_groups: tuple[ApprovalGroup, ...] = ()


def propose_metadata(context: ReleaseContext, policy: MetadataPolicy) -> MetadataProposal:
    """Return review decisions without reading or writing files."""
    edition = context.edition
    for style in (*policy.accepted_styles, *policy.store_style_mappings.values()):
        if not isinstance(style, str) or not style or style != style.strip() or "," in style:
            raise InvalidMetadataPolicy("Style names must be nonempty, trimmed, individual names without commas.")
    for category in policy.store_style_mappings:
        if not isinstance(category, str) or not category or category != category.strip():
            raise InvalidMetadataPolicy("Store mapping categories must be nonempty trimmed text.")
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
    evaluated_rules = []
    approval_groups = []
    if edition.genre_sources is not None or context.manual_genres is not None:
        evaluated_rules.append("genres")
        problem = None
        sources = edition.genre_sources or ()
        if context.manual_genres is not None:
            sources = (GenreSource("manual", edition.release_id, context.manual_genres),)
        preferred_sources = tuple(s for s in sources if s.provider == "discogs" and s.styles)
        if not preferred_sources:
            preferred_sources = tuple(s for s in sources if s.styles)
        source = next((s for s in sources if s.provider in ("discogs", "manual") and s.styles), None)
        reason = "Apply the matched Discogs styles release-wide, preserving order and removing duplicates."
        if source is not None:
            styles = source.styles
        else:
            source = next((s for s in sources if s.styles), None)
            styles = ()
            if source is None:
                problem = ("GENRE_EVIDENCE_REQUIRED", "No release styles are established.")
            elif any(style not in policy.store_style_mappings for style in source.styles):
                problem = ("GENRE_MAPPING_REQUIRED", "Choose an explicit mapping for every store category.")
            else:
                styles = tuple(policy.store_style_mappings[style] for style in source.styles)
            reason = "Apply explicit store-style mapping to accepted Discogs-style names."
        if context.manual_genres is not None:
            reason = "Apply the explicitly selected release-wide genres after vocabulary validation."
        if len({tuple(dict.fromkeys(s.styles)) for s in preferred_sources}) > 1:
            problem = ("GENRE_SOURCE_CONFLICT", f"Select between conflicting genre sources: {preferred_sources!r}")
        if problem is None and any(style not in policy.accepted_styles for style in styles):
            problem = ("GENRE_STYLE_NOT_ACCEPTED", "Choose styles from the configured accepted vocabulary.")
        value = (", ".join(dict.fromkeys(styles)),) if problem is None else None
        conflicts = tuple(
            f.file_id for f in context.files
            if f.file_id not in context.selected_file_ids and f.tags.get("%GENRE%") != value
        ) if problem is None else ()
        if conflicts:
            problem = ("GENRE_SELECTION_CONFLICT", "Unselected release tracks have different genres; expand selection or skip.")
            value = None
        if problem is None and not context.membership_complete:
            problem = ("MEMBERSHIP_INCOMPLETE", "Inspect all local release members before proposing release-wide genres.")
            value = None
        if problem is None and context.selected_file_ids - set(context.track_mapping):
            problem = ("GENRE_MAPPING_UNCONFIRMED", "Confirm all selected track mappings before changing release-wide genres.")
            value = None
        if problem:
            issues.append(ReviewIssue(
                problem[0], problem[1],
                tuple(f.file_id for f in context.files if f.file_id in context.selected_file_ids) + conflicts,
                ("%GENRE%",), ("expand_selection", "skip") if conflicts else
                ("refresh_membership", "skip") if problem[0] == "MEMBERSHIP_INCOMPLETE" else
                ("manual_mapping", "skip") if problem[0] == "GENRE_MAPPING_UNCONFIRMED" else
                ("select_genres", "skip"),
            ))
        for file in context.files:
            if file.file_id in context.selected_file_ids:
                existing = file.tags.get("%GENRE%")
                decisions.append(FieldDecision(
                    file.file_id, "%GENRE%", existing, value,
                    "UNRESOLVED" if problem else "KEEP" if existing == value else "SET",
                    Evidence(source.provider, source.release_id, source.styles) if source else
                    Evidence(edition.provider, edition.release_id, None),
                    problem[1] if problem else reason,
                ))
        changed = tuple(d.file_id for d in decisions if d.field == "%GENRE%" and d.state == "SET")
        if changed:
            approval_groups.append(ApprovalGroup("%GENRE%", changed, value))
    if edition.media_type not in ("Vinyl", "LP"):
        return MetadataProposal(tuple(decisions), tuple(issues), tuple(evaluated_rules), tuple(approval_groups))
    evaluated_rules.append("vinyl_numbering")
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
    return MetadataProposal(tuple(decisions), tuple(issues), tuple(evaluated_rules), tuple(approval_groups))
