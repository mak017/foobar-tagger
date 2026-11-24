"""Pure metadata proposals for implemented normalization rules."""

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Mapping
import re

from core.artist_credits import ArtistCredit, RenderedCredit, credit_source, credits_differ, is_various, render_artist_credit
from core.release_classification import ReleaseTypeEvidence, classify_release
from core.track_positions import DISC_MEDIA, parse_disc_position


class InvalidProposalInput(ValueError):
    """The context contains inconsistent file or edition identities."""


class InvalidMetadataPolicy(ValueError):
    """The style vocabulary or mapping configuration is malformed."""


@dataclass(frozen=True)
class LocalFile:
    file_id: str
    tags: Mapping[str, tuple[str, ...]]
    duration_seconds: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "tags", MappingProxyType({
            field: tuple(values) for field, values in self.tags.items()
        }))


@dataclass(frozen=True)
class EditionTrack:
    track_id: str
    position: str
    title: str = ""
    duration_seconds: int | None = None
    artist_credits: tuple[ArtistCredit, ...] | None = None

    def __post_init__(self):
        if self.artist_credits is not None:
            object.__setattr__(self, "artist_credits", tuple(self.artist_credits))


@dataclass(frozen=True)
class GenreSource:
    """Genre evidence already established as applicable to the selected edition."""

    provider: str
    release_id: str
    styles: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "styles", tuple(self.styles))


@dataclass(frozen=True)
class DateSource:
    """A date claim established as applicable to its identified release."""

    provider: str
    release_id: str
    value: str


@dataclass(frozen=True)
class ReleaseDates:
    """Content relationship is an evidenced conclusion, not a date comparison."""

    edition_date: DateSource | None = None
    original_date: DateSource | None = None
    content_relationship: str = "unknown"


@dataclass(frozen=True)
class Edition:
    provider: str
    release_id: str
    media_type: str
    tracks: tuple[EditionTrack, ...]
    tracklist_complete: bool
    genre_sources: tuple[GenreSource, ...] | None = None
    dates: ReleaseDates | None = None
    release_title: str | None = None
    artist_credits: tuple[ArtistCredit, ...] | None = None
    release_type_evidence: ReleaseTypeEvidence | None = None
    disc_count: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "tracks", tuple(self.tracks))
        if self.genre_sources is not None:
            object.__setattr__(self, "genre_sources", tuple(self.genre_sources))
        if self.artist_credits is not None:
            object.__setattr__(self, "artist_credits", tuple(self.artist_credits))


@dataclass(frozen=True)
class ManualDate:
    value: str
    clear_exact_date: bool = False


@dataclass(frozen=True)
class ReleaseContext:
    files: tuple[LocalFile, ...]
    selected_file_ids: frozenset[str]
    edition: Edition
    track_mapping: Mapping[str, str]
    membership_complete: bool = False
    manual_genres: tuple[str, ...] | None = None
    manual_date: ManualDate | None = None
    manual_release_type: str | None = None

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
    linked_changes: tuple[tuple[str, str, tuple[str, ...] | None], ...] = ()

    @property
    def changes(self) -> tuple[tuple[str, str, tuple[str, ...] | None], ...]:
        return self.linked_changes or tuple(
            (file_id, self.field, self.target_values) for file_id in self.file_ids
        )


@dataclass(frozen=True)
class MetadataProposal:
    decisions: tuple[FieldDecision, ...]
    issues: tuple[ReviewIssue, ...]
    evaluated_rules: tuple[str, ...] = ("vinyl_numbering",)
    approval_groups: tuple[ApprovalGroup, ...] = ()


@dataclass(frozen=True)
class _CreditValue:
    values: tuple[str, ...] | None
    source: str | tuple[str, ...] | None
    reason: str
    unresolved: bool = False


def _credited_text(source: str | None, reason: str, missing: str) -> _CreditValue:
    value = " ".join((source or "").split())
    return _CreditValue((value,), source, reason) if value else _CreditValue(None, source, missing, True)


def _is_various_edition(edition: Edition) -> bool:
    artists = edition.artist_credits
    return bool(artists and len(artists) == 1 and is_various(artists[0], edition.provider))


def _track_credit_sources(edition: Edition) -> tuple[bool, dict[str, tuple[ArtistCredit, ...] | None]]:
    inherit = (edition.provider == "discogs" and edition.tracklist_complete
               and all(track.artist_credits is None for track in edition.tracks))
    return inherit, {track.track_id: edition.artist_credits if inherit else track.artist_credits for track in edition.tracks}


def _album_artist(
    edition: Edition, artists: dict[str, RenderedCredit | None], artist_sources: dict[str, tuple[str, ...] | None],
) -> _CreditValue:
    release_source = credit_source(edition.artist_credits)
    known = tuple(artist for artist in artists.values() if artist is not None)
    if not edition.tracklist_complete or not edition.tracks:
        return _CreditValue(None, release_source, "Album artist needs the complete edition tracklist.", True)
    if not credits_differ(known):
        if len(known) != len(edition.tracks):
            return _CreditValue(None, release_source, "Some track artists are missing. The available credits do not establish album artist.", True)
        return _CreditValue(None, artist_sources[edition.tracks[0].track_id],
                            "All tracks in the complete edition have the same artist credit. Remove the album artist.")
    if _is_various_edition(edition):
        return _CreditValue(("Various",), release_source, "Track credits differ and the edition is credited to Various.")
    release_credit = render_artist_credit(edition.artist_credits, edition.provider)
    if release_credit is None:
        return _CreditValue(None, release_source, "Track credits differ. The main release artist credit is missing or unresolved.", True)
    return _CreditValue(release_credit.values, release_source,
                        "Track credits differ. Use the main artist credit for the selected edition.")


def _propose_credited_fields(
    context: ReleaseContext, decisions: list[FieldDecision], issues: list[ReviewIssue],
) -> None:
    edition = context.edition
    tracks = {track.track_id: track for track in edition.tracks}
    inherit, raw_credits = _track_credit_sources(edition)
    artists = {key: render_artist_credit(credit, edition.provider) for key, credit in raw_credits.items()}
    sources = {key: credit_source(credit) for key, credit in raw_credits.items()}
    album = _credited_text(edition.release_title, "Use the title of the matched edition.", "The edition title is unavailable.")
    album_artist = _album_artist(edition, artists, sources)
    unmapped = _CreditValue(None, None, "Confirm a track mapping before changing these fields.", True)
    for file in context.files:
        if file.file_id not in context.selected_file_ids:
            continue
        mapped_id = context.track_mapping.get(file.file_id)
        if mapped_id is None:
            fields = (("%ARTIST%", unmapped), ("%TITLE%", unmapped),
                      ("%ALBUM%", unmapped), ("%ALBUM ARTIST%", unmapped))
            issues.append(ReviewIssue("CREDIT_MAPPING_REQUIRED", unmapped.reason, (file.file_id,),
                                      tuple(field for field, _ in fields), ("manual_mapping", "skip")))
        else:
            artist = artists[mapped_id]
            if artist is None:
                reason = "The main artist credit for the matched track is missing or unresolved."
            elif inherit:
                reason = "Use the Discogs release artist credit. The edition supplies no separate track artist credits."
            else:
                reason = "Use the matched track's credited names and collaborators."
            artist_value = _CreditValue(artist.values if artist else None, sources[mapped_id], reason, artist is None)
            title = _credited_text(tracks[mapped_id].title, "Use the matched recording's full credited title.",
                                   "The matched track title is unavailable.")
            fields = (("%ARTIST%", artist_value), ("%TITLE%", title), ("%ALBUM%", album), ("%ALBUM ARTIST%", album_artist))
        for field, value in fields:
            existing = file.tags.get(field)
            if value.unresolved:
                state = "UNRESOLVED"
                if mapped_id is not None:
                    issues.append(ReviewIssue("CREDIT_EVIDENCE_REQUIRED", value.reason, (file.file_id,),
                                              (field,), ("review_edition", "skip")))
            else:
                state = "KEEP" if existing == value.values else "DELETE" if value.values is None else "SET"
            decisions.append(FieldDecision(file.file_id, field, existing, value.values, state,
                                           Evidence(edition.provider, edition.release_id, value.source), value.reason))


def _propose_release_type(
    context: ReleaseContext, decisions: list[FieldDecision], issues: list[ReviewIssue],
) -> None:
    edition = context.edition
    track_count = len(edition.tracks) if edition.tracklist_complete else None
    durations = tuple(track.duration_seconds for track in edition.tracks)
    duration = (sum(value for value in durations if value is not None)
                if track_count and all(value is not None and value > 0 for value in durations) else None)
    various = _is_various_edition(edition)
    single_artist = False
    if track_count and track_count > 6 and not various:
        _, sources = _track_credit_sources(edition)
        artists = tuple(artist for source in sources.values()
                        if (artist := render_artist_credit(source, edition.provider)) is not None and len(artist.identities) == 1)
        if len(artists) == track_count:
            identities = {artist.identities[0] for artist in artists}
            single_artist = len(identities) == 1 if None not in identities else not credits_differ(artists)
    classification = classify_release(
        edition.release_type_evidence or ReleaseTypeEvidence(), track_count=track_count, duration_seconds=duration,
        single_artist=single_artist, various_artists=various, manual=context.manual_release_type,
    )
    evidence = Evidence("manual" if context.manual_release_type is not None else edition.provider,
                        edition.release_id, classification.source)
    for file in context.files:
        if file.file_id not in context.selected_file_ids:
            continue
        field = "%RELEASETYPE%"
        existing = file.tags.get(field)
        candidate = (classification.value,) if classification.value is not None else None
        reason = classification.reason
        if file.file_id not in context.track_mapping:
            candidate = None
            reason = "Confirm a track mapping before changing the release type on this file."
            issues.append(ReviewIssue("RELEASE_TYPE_MAPPING_REQUIRED", reason, (file.file_id,), (field,), ("manual_mapping", "skip")))
        elif candidate is None:
            issues.append(ReviewIssue("RELEASE_TYPE_REQUIRED", reason, (file.file_id,), (field,), ("select_release_type", "skip")))
        state = "UNRESOLVED" if candidate is None else "KEEP" if candidate == existing else "SET"
        decisions.append(FieldDecision(file.file_id, field, existing, candidate, state, evidence, reason))


def _valid_release_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if re.fullmatch(r"[0-9]{4}", value):
        return value != "0000"
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


_TRACK_FIELDS = ("%TRACKNUMBER%", "%TOTALTRACKS%")
_DISC_FIELDS = ("%DISCNUMBER%", "%TOTALDISCS%")


def _disc_layout(edition: Edition) -> tuple[dict[str, tuple[int, int]], dict[int, int]] | None:
    positions: dict[str, tuple[int, int]] = {}
    track_numbers: dict[int, set[int]] = {}
    implicit_disc = False
    for track in edition.tracks:
        position = parse_disc_position(track.position, edition.media_type)
        if position is None:
            return None
        disc, number = position
        if disc is None:
            implicit_disc = True
            disc = 1
        numbers = track_numbers.setdefault(disc, set())
        if number in numbers:
            return None
        numbers.add(number)
        positions[track.track_id] = (disc, number)

    disc_count = len(track_numbers)
    if (
        not disc_count or (implicit_disc and disc_count > 1)
        or (edition.disc_count is not None and edition.disc_count != disc_count)
        or sorted(track_numbers) != list(range(1, disc_count + 1))
    ):
        return None
    if any(sorted(numbers) != list(range(1, len(numbers) + 1)) for numbers in track_numbers.values()):
        return None
    return positions, {disc: len(numbers) for disc, numbers in track_numbers.items()}


def _propose_disc_web_numbering(
    context: ReleaseContext, decisions: list[FieldDecision], issues: list[ReviewIssue], approval_groups: list[ApprovalGroup],
) -> None:
    edition = context.edition
    web = edition.media_type == "WEB"
    layout = _disc_layout(edition) if not web and edition.tracklist_complete else None
    tracks = {track.track_id: (ordinal, track) for ordinal, track in enumerate(edition.tracks, 1)}
    fields = (*_TRACK_FIELDS, *_DISC_FIELDS)
    for file in context.files:
        if file.file_id not in context.selected_file_ids:
            continue
        mapped = context.track_mapping.get(file.file_id)
        targets: dict[str, str | None] = {}
        evidence = Evidence(edition.provider, edition.release_id, None)
        reason = "Confirm a provider track mapping before changing this file."
        issue_code = "MAPPING_REQUIRED"
        options = ("manual_mapping", "skip")
        if mapped is not None:
            ordinal, track = tracks[mapped]
            evidence = Evidence(edition.provider, edition.release_id, track.position or None)
            reason = "A complete edition tracklist is required to establish numbering and totals."
            issue_code = "INCOMPLETE_TRACKLIST"
            options = ("complete_tracklist", "skip")
            if web:
                targets.update(dict.fromkeys(_DISC_FIELDS))
                if edition.tracklist_complete:
                    targets.update(zip(_TRACK_FIELDS, (str(ordinal), str(len(edition.tracks)))))
            elif layout is not None:
                positions, totals = layout
                disc, number = positions[mapped]
                targets.update(zip(_TRACK_FIELDS, (str(number), str(totals[disc]))))
                targets.update(zip(_DISC_FIELDS, (str(disc), str(len(totals))) if len(totals) > 1 else (None, None)))
            elif edition.tracklist_complete:
                reason = "The edition positions or carrier count do not establish consecutive track numbers and disc totals."
                issue_code = "DISC_LAYOUT_UNRESOLVED"
                options = ("review_edition", "skip")

        unresolved = tuple(field for field in fields if field not in targets)
        if unresolved:
            issues.append(ReviewIssue(issue_code, reason, (file.file_id,), unresolved, options))
        disc_changes: list[tuple[str, str, tuple[str, ...] | None]] = []
        for field in fields:
            existing = file.tags.get(field)
            if field not in targets:
                decisions.append(FieldDecision(file.file_id, field, existing, None, "UNRESOLVED", evidence, reason))
                continue
            target = targets[field]
            values = (target,) if target is not None else None
            state = "KEEP" if existing == values else "DELETE" if values is None else "SET"
            if field in _DISC_FIELDS:
                note = "Use the verified disc number and full edition disc count."
                if target is None:
                    note = "Remove disc tags from this WEB edition." if web else "Remove disc tags from this single-disc edition."
                if state in ("SET", "DELETE"):
                    disc_changes.append((file.file_id, field, values))
            else:
                note = ("Use the track order and total across the complete WEB edition." if web else
                        "Use the track number and total on this disc of the complete edition.")
            decisions.append(FieldDecision(file.file_id, field, existing, values, state, evidence, note))
        if len(disc_changes) == 2:
            approval_groups.append(ApprovalGroup("%DISCNUMBER%", (file.file_id,), (), tuple(disc_changes)))


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
    decisions: list[FieldDecision] = []
    issues: list[ReviewIssue] = []
    evaluated_rules: list[str] = []
    approval_groups: list[ApprovalGroup] = []
    if (edition.release_title is not None or edition.artist_credits is not None
            or any(track.title or track.artist_credits is not None for track in edition.tracks)):
        evaluated_rules.append("credited_metadata")
        _propose_credited_fields(context, decisions, issues)
    if edition.release_type_evidence is not None or context.manual_release_type is not None:
        evaluated_rules.append("release_type")
        _propose_release_type(context, decisions, issues)
    if edition.dates is not None or context.manual_date is not None:
        evaluated_rules.append("release_dates")
        dates = edition.dates or ReleaseDates()
        date_source = dates.edition_date
        date_problem: tuple[str, str] | None = None
        reason = "Use the edition's own date for original or changed release content."
        if dates.content_relationship == "unchanged":
            date_source = dates.original_date
            reason = "Use the supported original release date for unchanged release content."
        elif dates.content_relationship not in ("original", "changed"):
            date_problem = ("DATE_RELATIONSHIP_REQUIRED", "Establish whether release content is original, unchanged, or changed.")
            if dates.content_relationship == "unknown" and date_source and dates.original_date and date_source.value == dates.original_date.value:
                date_problem = None
                reason = "Edition and supported original dates agree; no content relationship assumption is needed."
        if context.manual_date is not None:
            date_source = DateSource("manual", edition.release_id, context.manual_date.value)
            date_problem = None
            reason = "Apply the explicitly selected release-wide date after validation."
        if date_source is None and date_problem is None:
            date_problem = ("DATE_EVIDENCE_REQUIRED", "The applicable release date is not established.")
        if date_source is not None and date_problem is None and not _valid_release_date(date_source.value):
            date_problem = ("DATE_VALUE_INVALID", "Use a valid YYYY year or complete calendar date YYYY-MM-DD.")
        if date_problem is None and date_source is not None and context.manual_date and context.manual_date.clear_exact_date and len(date_source.value) != 4:
            date_problem = ("DATE_MANUAL_CONFLICT", "Choose either a complete date or a year with explicit exact-date deletion.")
        date_targets: dict[str, tuple[str, ...] | None] = {"%DATE%": (date_source.value[:4],), "%RELEASE DATE%": (date_source.value,)} if date_source and date_problem is None else {
            "%DATE%": None, "%RELEASE DATE%": None,
        }
        if date_source and date_problem is None and len(date_source.value) == 4:
            date_targets["%RELEASE DATE%"] = None
            if date_problem is None and not (context.manual_date and context.manual_date.clear_exact_date) and any("%RELEASE DATE%" in file.tags for file in context.files):
                date_problem = ("DATE_EXACT_UNCONFIRMED", "Year-only evidence cannot replace or delete an existing exact-date field; confirm or explicitly clear it.")
        date_conflicts: tuple[str, ...] = ()
        if date_problem is None:
            date_conflicts = tuple(
                file.file_id for file in context.files
                if file.file_id not in context.selected_file_ids and any(
                    file.tags.get(key) != value for key, value in date_targets.items()
                )
            )
            if not context.membership_complete:
                date_problem = ("MEMBERSHIP_INCOMPLETE", "Inspect all local release members before changing release-wide dates.")
            elif context.selected_file_ids - set(context.track_mapping):
                date_problem = ("DATE_MAPPING_UNCONFIRMED", "Confirm all selected track mappings before changing release-wide dates.")
            elif date_conflicts:
                date_problem = ("DATE_SELECTION_CONFLICT", "Unselected release tracks have different dates; expand selection or skip.")
        if date_problem:
            issues.append(ReviewIssue(
                date_problem[0], date_problem[1],
                tuple(f.file_id for f in context.files if f.file_id in context.selected_file_ids) + date_conflicts,
                ("%DATE%", "%RELEASE DATE%"),
                ("refresh_membership", "skip") if date_problem[0] == "MEMBERSHIP_INCOMPLETE" else
                ("manual_mapping", "skip") if date_problem[0] == "DATE_MAPPING_UNCONFIRMED" else
                ("expand_selection", "skip") if date_problem[0] == "DATE_SELECTION_CONFLICT" else
                ("confirm_dates", "skip"),
            ))
        for file in context.files:
            if file.file_id not in context.selected_file_ids:
                continue
            for date_field, value in date_targets.items():
                existing = file.tags.get(date_field)
                decisions.append(FieldDecision(
                    file.file_id, date_field, existing, None if date_problem else value,
                    "UNRESOLVED" if date_problem else "KEEP" if existing == value else
                    "DELETE" if value is None else "SET",
                    Evidence(date_source.provider, date_source.release_id, date_source.value) if date_source else
                    Evidence(edition.provider, edition.release_id, None),
                    date_problem[1] if date_problem else reason,
                ))
        date_changes = tuple(
            (d.file_id, d.field, d.proposed_values) for d in decisions
            if d.field in ("%DATE%", "%RELEASE DATE%") and d.state in ("SET", "DELETE")
        )
        year_target = date_targets["%DATE%"]
        if date_changes and year_target is not None:
            approval_groups.append(ApprovalGroup(
                "%DATE%", tuple(dict.fromkeys(change[0] for change in date_changes)),
                year_target, date_changes,
            ))
    if edition.genre_sources is not None or context.manual_genres is not None:
        evaluated_rules.append("genres")
        problem: tuple[str, str] | None = None
        sources = edition.genre_sources or ()
        if context.manual_genres is not None:
            sources = (GenreSource("manual", edition.release_id, context.manual_genres),)
        preferred_sources = tuple(s for s in sources if s.provider == "discogs" and s.styles)
        if not preferred_sources:
            preferred_sources = tuple(s for s in sources if s.styles)
        genre_source = next((s for s in sources if s.provider in ("discogs", "manual") and s.styles), None)
        reason = "Apply the matched Discogs styles release-wide, preserving order and removing duplicates."
        if genre_source is not None:
            styles = genre_source.styles
        else:
            genre_source = next((s for s in sources if s.styles), None)
            styles = ()
            if genre_source is None:
                problem = ("GENRE_EVIDENCE_REQUIRED", "No release styles are established.")
            elif any(style not in policy.store_style_mappings for style in genre_source.styles):
                problem = ("GENRE_MAPPING_REQUIRED", "Choose an explicit mapping for every store category.")
            else:
                styles = tuple(policy.store_style_mappings[style] for style in genre_source.styles)
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
                    Evidence(genre_source.provider, genre_source.release_id, genre_source.styles) if genre_source else
                    Evidence(edition.provider, edition.release_id, None),
                    problem[1] if problem else reason,
                ))
        changed = tuple(d.file_id for d in decisions if d.field == "%GENRE%" and d.state == "SET")
        if changed and value is not None:
            approval_groups.append(ApprovalGroup("%GENRE%", changed, value))
    if edition.media_type in DISC_MEDIA or edition.media_type == "WEB":
        evaluated_rules.append("web_numbering" if edition.media_type == "WEB" else "disc_numbering")
        _propose_disc_web_numbering(context, decisions, issues, approval_groups)
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
