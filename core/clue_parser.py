"""Extract search clues from a release folder name."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FolderClues:
    catalog: str | None
    artist: str | None
    album: str | None
    year: str | None
    media_type: str | None


def parse_folder_clues(folder_name: str) -> FolderClues:
    name = folder_name.strip()
    catalog_match = re.match(r"^\[([^\[\]]+)\]\s+", name)
    catalog = None
    if catalog_match:
        catalog = catalog_match[1].strip() or None
        name = name[catalog_match.end():]

    media_match = re.search(r"\s+(WEB|CD|Vinyl|LP|DVD)$", name, flags=re.IGNORECASE)
    media_type = None
    if media_match:
        media_type = "Vinyl" if media_match[1].lower() == "vinyl" else media_match[1].upper()
        name = name[:media_match.start()]

    year_match = re.search(r"\s+\(([1-9][0-9]{3})\)$", name)
    year = None
    if year_match:
        year = year_match[1]
        name = name[:year_match.start()]

    artist, separator, album = name.partition(" - ")
    if not separator:
        return FolderClues(catalog, None, name.strip() or None, year, media_type)
    return FolderClues(catalog, artist.strip() or None, album.strip() or None, year, media_type)
