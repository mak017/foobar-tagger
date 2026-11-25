"""Suggest reviewable one-to-one mappings within a chosen edition."""

from collections import Counter
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import PurePosixPath
import re
from typing import Literal
from unicodedata import normalize

from core.artist_credits import render_artist_credit
from core.rules_engine import Edition, EditionTrack, LocalFile, ReleaseContext
from core.track_positions import DISC_MEDIA, parse_disc_position


MAX_DURATION_DIFFERENCE = 5
VINYL_MEDIA = frozenset({"Vinyl", "LP"})
_BRACKETED = re.compile(r"^(.+?)\s*(?:\(([^()]+)\)|\[([^\[\]]+)\])$")
_VERSION_WORD = re.compile(r"\b(?:mix|remix|edit|version|dub|rework|instrumental|vocal|remaster(?:ed)?|live|acoustic|vip)\b")
_REMASTER_VERSION = re.compile(r"(?:[0-9]{4} )?remaster(?:ed)?(?: [0-9]{4})?")
_FILE_PREFIX = re.compile(r"^(\d{1,2}[-.]\d{1,3}|[a-z]{1,2}\d{0,3}|\d{1,3})([ ._\-]+)(\S.*)$", re.IGNORECASE)
_PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "–": "-", "—": "-", "‐": "-"})


@dataclass(frozen=True)
class TrackEvidence:
    track: EditionTrack
    title: Literal["match", "version_missing", "version_conflict"]
    duration_difference: float | None
    position: Literal["match", "unknown", "conflict"]
    claimed: bool
    version_explicit: bool

    @property
    def plausible(self) -> bool:
        return (
            not self.claimed and self.title == "match" and self.position != "conflict"
            and (self.duration_difference is None or self.duration_difference <= MAX_DURATION_DIFFERENCE)
        )


@dataclass(frozen=True)
class TrackSuggestion:
    file_id: str
    state: Literal["suggested", "ambiguous", "unresolved"]
    suggested_id: str | None
    title_source: str
    reason: str
    candidates: tuple[TrackEvidence, ...]


def _title_parts(value: str) -> tuple[str, str]:
    title = " ".join(normalize("NFC", value).translate(_PUNCTUATION).casefold().split())
    suffix = _BRACKETED.fullmatch(title)
    if suffix is not None:
        return suffix[1].rstrip().removesuffix(" -").rstrip(), (suffix[2] or suffix[3]).strip()
    base, separator, version = title.rpartition(" - ")
    if separator and _VERSION_WORD.search(version):
        return base, version
    return title, ""


def _content_titles(edition: Edition) -> tuple[tuple[str, str], ...]:
    titles = (_title_parts(track.title) for track in edition.tracks)
    return tuple(_title_parts(title) if _REMASTER_VERSION.fullmatch(version) else (title, version)
                 for title, version in titles)


def _recording_artists(edition: Edition) -> tuple[tuple[int | None, ...] | None, ...]:
    inherit = all(track.artist_credits is None for track in edition.tracks)
    return tuple(
        credit.identities if (credit := render_artist_credit(
            edition.artist_credits if inherit else track.artist_credits, edition.provider,
        )) and None not in credit.identities else None
        for track in edition.tracks
    )


def compare_release_content(selected: Edition, original: Edition) -> Literal["unchanged", "changed", "unknown"]:
    """Compare two complete listings without using positions, formats, or dates as recording evidence."""
    if not selected.tracklist_complete or not original.tracklist_complete or not selected.tracks or not original.tracks:
        return "unknown"
    selected_titles, original_titles = _content_titles(selected), _content_titles(original)
    if any(not title for title, _ in (*selected_titles, *original_titles)):
        return "unknown"
    if len(selected_titles) != len(original_titles):
        return "changed"
    if selected_titles != original_titles:
        if Counter(selected_titles) == Counter(original_titles):
            return "unknown"
        if any(title != old_title or (version and old_version and version != old_version)
               for (title, version), (old_title, old_version) in zip(selected_titles, original_titles)):
            return "changed"
        return "unknown"

    artists, original_artists = _recording_artists(selected), _recording_artists(original)
    if None in artists or artists != original_artists:
        return "unknown"
    for track, original_track in zip(selected.tracks, original.tracks):
        difference = _duration_difference(track.duration_seconds, original_track.duration_seconds)
        if difference is None or difference > MAX_DURATION_DIFFERENCE:
            return "unknown"
    return "unchanged"


def _filename_parts(file_id: str) -> tuple[str, str]:
    name = PurePosixPath(file_id).stem.translate(_PUNCTUATION).strip()
    match = _FILE_PREFIX.fullmatch(name)
    if match is None:
        return name, ""
    position, separator, title = match.groups()
    if separator.isspace() and (position.isalpha() or (position.isdecimal() and not position.startswith("0"))):
        return name, ""
    return title, position


def _positive_number(value: str) -> str | None:
    if re.fullmatch(r"[0-9]{1,4}", value) and int(value) > 0:
        return str(int(value))
    return None


def _position_parts(value: str, media: str, *, from_filename: bool = False) -> dict[str, str]:
    value = value.strip().upper()
    if media in VINYL_MEDIA:
        side = re.fullmatch(r"([A-Z]+)([0-9]{1,4})?", value)
        if side is not None:
            result = {"side": side[1]}
            if side[2] and (within := _positive_number(side[2])) is not None:
                result["within_side"] = within
            return result
    if media in DISC_MEDIA:
        if from_filename:
            value = value.replace(".", "-")
        position = parse_disc_position(value, media)
        if position is None:
            return {}
        disc, track = position
        result = {"track": str(track)}
        if disc is not None:
            result["disc"] = str(disc)
        return result
    track_number = _positive_number(value)
    return {"track": track_number} if track_number is not None else {}


def _local_positions(file: LocalFile, filename_position: str, media: str) -> dict[str, set[str]]:
    result = {key: {value} for key, value in _position_parts(filename_position, media, from_filename=True).items()}
    fields = {"%TRACKNUMBER%": "track"}
    if media in DISC_MEDIA:
        fields["%DISCNUMBER%"] = "disc"
    if media in VINYL_MEDIA:
        fields["%VINYLSIDE%"] = "side"
        fields["%VINYLTRACKNUMBER%"] = "within_side"
    for field, key in fields.items():
        for value in file.tags.get(field, ()):
            value = value.strip()
            if key == "side":
                normalized = value.upper() if re.fullmatch(r"[A-Za-z]+", value) else None
            else:
                normalized = _positive_number(value.split("/", 1)[0].strip())
            if normalized is not None:
                result.setdefault(key, set()).add(normalized)
    return result


def _edition_positions(edition: Edition, track: EditionTrack, ordinal: int) -> dict[str, str]:
    if edition.media_type == "WEB" and edition.tracklist_complete:
        return {"track": str(ordinal)}
    positions = _position_parts(track.position, edition.media_type)
    if edition.media_type in VINYL_MEDIA and edition.tracklist_complete:
        positions.setdefault("track", str(ordinal))
    return positions


def _position_relation(local: dict[str, set[str]], edition: dict[str, str]) -> Literal["match", "unknown", "conflict"]:
    shared = local.keys() & edition.keys()
    if any(local[key] != {edition[key]} for key in shared):
        return "conflict"
    return "match" if shared else "unknown"


def _duration_difference(local: float | None, edition: int | None) -> float | None:
    if local is None or edition is None or not isfinite(local) or local <= 0 or edition <= 0:
        return None
    return abs(local - edition)


def suggest_track_mappings(context: ReleaseContext) -> tuple[TrackSuggestion, ...]:
    """Compare selected, unmapped files without changing the review mappings."""
    edition = context.edition
    reserved = set(context.track_mapping.values())
    tracks = tuple((track, _title_parts(track.title), _edition_positions(edition, track, ordinal))
                   for ordinal, track in enumerate(edition.tracks, 1) if track.title.strip())
    suggestions: list[TrackSuggestion] = []
    for file in context.files:
        if file.file_id not in context.selected_file_ids or file.file_id in context.track_mapping:
            continue
        filename_title, filename_position = _filename_parts(file.file_id)
        titles = {_title_parts(title) for title in file.tags.get("%TITLE%", ()) if title.strip()}
        source = "title tag" if titles else "filename"
        if len(titles) > 1:
            suggestions.append(TrackSuggestion(file.file_id, "unresolved", None, source, "Local title tags disagree.", ()))
            continue
        base, version = next(iter(titles)) if titles else _title_parts(filename_title)
        position = _local_positions(file, filename_position, edition.media_type)
        candidates: list[TrackEvidence] = []
        for track, (track_base, track_version), track_position in tracks:
            if not base or base != track_base:
                continue
            title: Literal["match", "version_missing", "version_conflict"] = "match"
            if version != track_version:
                title = "version_conflict" if version and track_version else "version_missing"
            candidates.append(TrackEvidence(
                track, title, _duration_difference(file.duration_seconds, track.duration_seconds),
                _position_relation(position, track_position), track.track_id in reserved, bool(version and track_version),
            ))
        plausible = [candidate for candidate in candidates if candidate.plausible]
        state: Literal["suggested", "ambiguous", "unresolved"] = "unresolved"
        suggested_id = None
        if len(plausible) > 1:
            state, reason = "ambiguous", "Multiple edition tracks still fit the available evidence."
        elif plausible:
            candidate = plausible[0]
            if not edition.tracklist_complete:
                reason = "The edition tracklist is incomplete. Confirm this track manually."
            elif candidate.duration_difference is not None or candidate.position == "match":
                state = "suggested"
                title_reason = "Title and version agree" if candidate.version_explicit else "Title agrees. Neither title names a version."
                reason = f"{title_reason.rstrip('.')}. Duration or position supports the match."
                suggested_id = candidate.track.track_id
            else:
                reason = "Title matches. Duration or position evidence is missing."
        elif not candidates:
            reason = "No edition track has the same title."
        elif all(candidate.claimed for candidate in candidates):
            reason = "Matching titles are already mapped to other files."
        else:
            reason = "Version, duration, or position evidence needs manual review."
        suggestions.append(TrackSuggestion(file.file_id, state, suggested_id, source, reason, tuple(candidates)))

    claims = Counter(result.suggested_id for result in suggestions if result.suggested_id is not None)
    return tuple(
        replace(result, state="ambiguous", suggested_id=None,
                reason="Multiple selected files point to the same edition track.")
        if result.suggested_id is not None and claims[result.suggested_id] > 1 else result
        for result in suggestions
    )
