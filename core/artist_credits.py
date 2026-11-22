"""Render structured main artist credits without guessing artist splits."""

from dataclasses import dataclass
import re


_DISCOGS_DISAMBIGUATOR = re.compile(r" \((?:[2-9]|[1-9][0-9]+)\)$")
_DISCOGS_VARIOUS_ID = 194


@dataclass(frozen=True)
class ArtistCredit:
    name: str
    credited_as: str = ""
    join: str = ""
    artist_id: int | None = None

    @property
    def source_name(self) -> str:
        return self.credited_as if self.credited_as.strip() else self.name


@dataclass(frozen=True)
class RenderedCredit:
    values: tuple[str, ...]
    identities: tuple[int | None, ...]


def credit_source(artists: tuple[ArtistCredit, ...] | None) -> tuple[str, ...] | None:
    if not artists:
        return None
    return tuple(part for artist in artists for part in (artist.source_name, artist.join) if part.strip())


def is_various(artist: ArtistCredit, provider: str) -> bool:
    return provider == "discogs" and (
        artist.artist_id == _DISCOGS_VARIOUS_ID
        or (artist.artist_id is None and artist.name.strip().casefold() == "various")
    )


def render_artist_credit(artists: tuple[ArtistCredit, ...] | None, provider: str) -> RenderedCredit | None:
    if not artists or artists[-1].join.strip():
        return None
    values: list[str] = []
    connector = ""
    for artist in artists:
        name = " ".join(artist.source_name.split())
        if provider == "discogs":
            if is_various(artist, provider) or artist.name.strip().casefold() in ("unknown artist", "no artist"):
                return None
            if not artist.credited_as.strip():
                name = _DISCOGS_DISAMBIGUATOR.sub("", name)
        if not name:
            return None
        if connector == ",":
            values[-1] += f", {name}"
        elif connector:
            values[-1] += f" {connector} {name}"
        else:
            values.append(name)
        connector = " ".join(artist.join.split())
    return RenderedCredit(tuple(values), tuple(artist.artist_id for artist in artists))


def credits_differ(credits: tuple[RenderedCredit, ...]) -> bool:
    if len({credit.values for credit in credits}) > 1 or len({len(credit.identities) for credit in credits}) > 1:
        return True
    return any(len({artist_id for artist_id in artists if artist_id is not None}) > 1
               for artists in zip(*(credit.identities for credit in credits)))
