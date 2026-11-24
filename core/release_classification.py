"""Classify a release from the selected edition's evidence."""

from dataclasses import dataclass


VALID_RELEASE_TYPES = (
    "Single", "Sampler", "EP", "Album", "Compilation", "Album, Mixed", "DJ Mix",
    "Compilation, Mixed", "Radioshow", "Radioshow, Live Mix", "Live Mix", "Mixtape",
    "Sampler, EP", "EP, Mixed",
)

_FORMAT_LABELS = {label.casefold(): label for label in VALID_RELEASE_TYPES}
_FORMAT_LABELS.update({"maxi-single": "Single", "mixed": "Mixed", "dj mix": "Mixed",
                       "mini-album": "Mini-Album", "partially mixed": "Partially Mixed"})
_FORMAT_TYPES: dict[frozenset[str], str] = {frozenset(label.split(", ")): label for label in VALID_RELEASE_TYPES}
_FORMAT_TYPES.update({
    frozenset({"Mixed"}): "DJ Mix",
    frozenset({"Album", "Compilation"}): "Compilation",
    frozenset({"Album", "Compilation", "Mixed"}): "Compilation, Mixed",
})


@dataclass(frozen=True)
class ReleaseTypeEvidence:
    """Format descriptions grouped by carrier, plus any edition-wide DJ Mix credit."""

    formats: tuple[tuple[str, ...], ...] = ()
    dj_mix_credit: str | None = None
    edition_descriptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", tuple(tuple(descriptions) for descriptions in self.formats))
        object.__setattr__(self, "edition_descriptions", tuple(self.edition_descriptions))


@dataclass(frozen=True)
class ReleaseClassification:
    value: str | None
    reason: str
    source: str | tuple[str, ...] | None


def _format_claims(descriptions: tuple[str, ...]) -> frozenset[str]:
    return frozenset(part for label in descriptions if (canonical := _FORMAT_LABELS.get(label.strip().casefold()))
                     for part in canonical.split(", "))


def _explicit_type(evidence: ReleaseTypeEvidence) -> ReleaseClassification | None:
    raw_labels = tuple(dict.fromkeys(label for row in (evidence.edition_descriptions, *evidence.formats) for label in row
                                    if label.strip().casefold() in _FORMAT_LABELS))
    groups = [_format_claims(row) for row in evidence.formats]
    credit = evidence.dj_mix_credit
    if not raw_labels and not credit:
        return None
    sources = raw_labels + ((credit,) if credit else ())
    scope = _format_claims(evidence.edition_descriptions)
    if credit:
        scope |= {"Mixed"}
    if "Mixed" not in scope and any("Mixed" in group for group in groups) and not all("Mixed" in group for group in groups):
        return ReleaseClassification(None, "The format describes mixing for only part of the edition. Choose a release type in review.", sources)

    described_groups = [group for group in groups if group] or [frozenset[str]()]
    claims = {_FORMAT_TYPES.get(group | scope) for group in described_groups}
    if len(claims) != 1 or None in claims:
        return ReleaseClassification(None, "The edition's explicit descriptions conflict or have no supported combination. Choose a release type in review.", sources)
    value = next(iter(claims))
    if raw_labels and credit:
        reason = "Use the edition's format descriptions and edition-wide DJ Mix credit."
    elif credit:
        reason = "Use the edition-wide DJ Mix credit."
    else:
        reason = "Use the selected edition's explicit format descriptions."
    return ReleaseClassification(value, reason, sources)


def classify_release(
    evidence: ReleaseTypeEvidence, *, track_count: int | None, duration_seconds: int | None,
    single_artist: bool, various_artists: bool, manual: str | None = None,
) -> ReleaseClassification:
    if manual is not None:
        if manual in VALID_RELEASE_TYPES:
            return ReleaseClassification(manual, "Use the explicitly selected release type.", manual)
        return ReleaseClassification(None, "Choose one of the 14 supported release types.", manual)
    explicit = _explicit_type(evidence)
    if explicit is not None:
        return explicit
    if track_count is None or track_count == 0:
        return ReleaseClassification(None, "A complete edition tracklist is needed for release type fallback. Choose a type in review.", None)

    clues = [f"{track_count} playable tracks"]
    if duration_seconds is not None:
        clues.append(f"{duration_seconds} seconds total")
    if various_artists:
        clues.append("Release credited to Various")
    if track_count <= 2:
        return ReleaseClassification("Single", "The complete edition has one or two tracks and no explicit type.", tuple(clues))
    short_ep = 3 <= track_count <= 6 and duration_seconds is not None and duration_seconds < 30 * 60
    if various_artists:
        if short_ep:
            return ReleaseClassification(None, "The short tracklist suggests EP and the Various credit suggests Compilation. Choose a type in review.", tuple(clues))
        return ReleaseClassification("Compilation", "The complete edition has more than two tracks and is credited to Various.", tuple(clues))
    if short_ep:
        return ReleaseClassification("EP", "The complete edition has three to six tracks totaling less than 30 minutes.", tuple(clues))
    if track_count > 6 and single_artist:
        return ReleaseClassification("Album", "The complete edition has more than six tracks by one credited artist.",
                                     (*clues, "One artist across the complete edition"))
    return ReleaseClassification(None, "The available edition evidence does not establish a release type. Choose one in review.", tuple(clues))
