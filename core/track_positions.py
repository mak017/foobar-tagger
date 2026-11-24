"""Interpret numbered positions on Discogs CD and DVD tracklists."""

import re


DISC_MEDIA = frozenset({"CD", "DVD"})


def parse_disc_position(position: str, media: str) -> tuple[int | None, int] | None:
    position = position.strip().upper()
    match = re.fullmatch(r"(?:(?:(CD|DVD)\s*)?([0-9]{1,4})\s*-\s*)?([0-9]{1,4})", position)
    if match is None:
        return None
    carrier, disc, track = match.groups()
    disc_number = int(disc) if disc is not None else None
    track_number = int(track)
    if (carrier is not None and carrier != media) or disc_number == 0 or track_number == 0:
        return None
    return disc_number, track_number
