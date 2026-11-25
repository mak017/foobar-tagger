"""Search Discogs release editions and load their metadata and artwork."""

from dataclasses import dataclass, replace
from datetime import date
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib import request

from core.artist_credits import ArtistCredit
from core.matching import compare_release_content
from core.release_classification import ReleaseTypeEvidence
from core.rules_engine import DateSource, Edition, EditionTrack, GenreSource, ReleaseDates
from core.track_positions import DISC_MEDIA


@dataclass(frozen=True)
class ReleaseImage:
    url: str
    primary: bool


@dataclass(frozen=True)
class DiscogsRelease:
    title: str
    edition: Edition
    images: tuple[ReleaseImage, ...] = ()


@dataclass(frozen=True)
class ReleaseQuery:
    text: str = ""
    catalog: str = ""
    artist: str = ""
    title: str = ""
    format_name: str = ""


@dataclass(frozen=True)
class ReleaseCandidate:
    release_id: str
    title: str
    catalog: str
    formats: tuple[str, ...]
    labels: tuple[str, ...]
    year: str
    country: str

    @property
    def url(self) -> str:
        return f"https://www.discogs.com/release/{self.release_id}"


@dataclass(frozen=True)
class SearchPage:
    candidates: tuple[ReleaseCandidate, ...]
    page: int
    pages: int


class DiscogsError(ValueError):
    """Discogs could not supply the requested release."""


MAX_IMAGE_DOWNLOAD = 32 * 1024 * 1024
_LATER_OR_NONRETAIL = frozenset({"reissue", "repress", "promo", "advance", "test pressing",
                                "unofficial release", "partially unofficial"})


def _image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = parsed.netloc.lower()
    return parsed.scheme == "https" and (
        host in ("i.discogs.com", "img.discogs.com")
        or (host == "api.discogs.com" and parsed.path.startswith("/images/"))
    )


def _request(url: str, accept: str) -> request.Request:
    req = request.Request(url, headers={"User-Agent": "foobar-tagger/0.1", "Accept": accept})
    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if token and req.host == "api.discogs.com":
        if not re.fullmatch(r"[!-~]+", token):
            raise DiscogsError("DISCOGS_TOKEN must be a single token without spaces.")
        req.add_unredirected_header("Authorization", f"Discogs token={token}")
    return req


def _json(req: request.Request, action: str) -> object:
    try:
        with request.urlopen(req, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        exc.close()
        if exc.code == 429:
            raise DiscogsError("Discogs rate limit reached. Wait before retrying.") from exc
        if exc.code in (401, 403):
            hint = ("Check the token and API access." if req.has_header("Authorization") else
                    "Set a personal DISCOGS_TOKEN if authentication is required.")
            raise DiscogsError(f"Discogs denied {action} (HTTP {exc.code}). {hint}") from exc
        raise DiscogsError(f"Discogs returned HTTP {exc.code} for {action}. Check the request or retry.") from exc
    except (URLError, OSError) as exc:
        raise DiscogsError(f"Could not reach Discogs for {action}: {exc}. Retry the request.") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise DiscogsError(f"Discogs returned invalid JSON for {action}. Retry the request.") from exc


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(text for item in value if (text := _text(item))))


def _duration(value: object) -> int | None:
    duration = _text(value)
    if not re.fullmatch(r"(?:[0-9]{1,3}:)?[0-9]{1,3}:[0-5][0-9]", duration):
        return None
    parts = [int(part) for part in duration.split(":")]
    if len(parts) == 3 and parts[1] >= 60:
        return None
    seconds = sum(part * 60 ** index for index, part in enumerate(reversed(parts)))
    return seconds or None


def search_releases(query: ReleaseQuery, page: int = 1) -> SearchPage:
    """Return a page of edition candidates. The caller must select an edition."""
    filters = {name: value.strip() for name, value in (
        ("q", query.text), ("catno", query.catalog), ("artist", query.artist),
        ("release_title", query.title), ("format", query.format_name),
    ) if value.strip()}
    if not filters or page < 1:
        raise DiscogsError("Enter at least one search field and use a positive page number.")
    query_string = urlencode({**filters, "type": "release", "page": page, "per_page": 25})
    request_url = f"https://api.discogs.com/database/search?{query_string}"
    data = _json(_request(request_url, "application/json"), "release search")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise DiscogsError("Discogs did not return a release search result list.")
    pagination = data.get("pagination")
    if not isinstance(pagination, dict) or pagination.get("page") != page:
        raise DiscogsError("Discogs did not return the requested result page.")
    pages = pagination.get("pages")
    if type(pages) is not int or pages < 0:
        raise DiscogsError("Discogs returned invalid search pagination.")

    candidates: dict[str, ReleaseCandidate] = {}
    for item in data["results"]:
        if not isinstance(item, dict) or item.get("type") != "release":
            continue
        identity, title, year = item.get("id"), _text(item.get("title")), item.get("year")
        if type(identity) is not int or identity < 1 or not title:
            continue
        release_id = str(identity)
        if release_id not in candidates:
            year_text = str(year) if type(year) is int else _text(year)
            candidates[release_id] = ReleaseCandidate(
                release_id, title, _text(item.get("catno")), _text_list(item.get("format")),
                _text_list(item.get("label")), year_text if re.fullmatch(r"[1-9][0-9]{3}", year_text) else "",
                _text(item.get("country")),
            )
    return SearchPage(tuple(candidates.values()), page, max(page, pages))


class _ImageRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _image_url(newurl):
            raise DiscogsError("Discogs artwork redirected outside its image service.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_image(image: ReleaseImage) -> bytes:
    """Fetch a Discogs image without trusting advertised size or type."""
    if not _image_url(image.url):
        raise DiscogsError("Discogs did not provide a supported image URL.")
    opener = request.build_opener(_ImageRedirect())
    try:
        with opener.open(_request(image.url, "image/*"), timeout=10) as response:
            data = response.read(MAX_IMAGE_DOWNLOAD + 1)
    except HTTPError as exc:
        exc.close()
        raise DiscogsError(f"Discogs artwork returned HTTP {exc.code}. Try the image again or choose another.") from exc
    except OSError as exc:
        raise DiscogsError(f"Could not download Discogs artwork: {exc}") from exc
    if len(data) > MAX_IMAGE_DOWNLOAD:
        raise DiscogsError("The Discogs artwork download exceeded 32 MiB.")
    return data


def _release_images(entries: object) -> tuple[ReleaseImage, ...]:
    if not isinstance(entries, list):
        return ()
    images: dict[str, ReleaseImage] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        for url in (item.get("uri"), item.get("resource_url")):
            if isinstance(url, str) and _image_url(url):
                primary = item.get("type") == "primary"
                if url not in images or primary:
                    images[url] = ReleaseImage(url, primary)
                break
    return tuple(sorted(images.values(), key=lambda image: not image.primary))


def _artist_credits(entries: object) -> tuple[ArtistCredit, ...] | None:
    if entries is None or entries == []:
        return None
    if not isinstance(entries, list):
        return ()
    artists: list[ArtistCredit] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return ()
        name, credited_as, join, artist_id = (entry.get(key) for key in ("name", "anv", "join", "id"))
        if (not isinstance(name, str) or not name.strip()
                or (credited_as is not None and not isinstance(credited_as, str))
                or (join is not None and not isinstance(join, str))):
            return ()
        artists.append(ArtistCredit(name, credited_as or "", join or "",
                                    artist_id if type(artist_id) is int and artist_id > 0 else None))
    return tuple(artists)


def _release_type_evidence(formats: object, credits: object) -> ReleaseTypeEvidence:
    descriptions = []
    edition_descriptions: list[str] = []
    if isinstance(formats, list):
        for row in formats:
            if isinstance(row, dict):
                labels = _text_list(row.get("descriptions"))
                if _text(row.get("name")).casefold() in ("all media", "box set"):
                    edition_descriptions.extend(labels)
                else:
                    descriptions.append(labels)
    mixing_credit = None
    if isinstance(credits, list):
        for credit in credits:
            if isinstance(credit, dict) and _text(credit.get("role")).casefold() == "dj mix":
                tracks = credit.get("tracks")
                if tracks is None or (isinstance(tracks, str) and not tracks.strip()):
                    mixing_credit = _text(credit.get("role"))
                    break
    return ReleaseTypeEvidence(tuple(descriptions), mixing_credit, tuple(edition_descriptions))


def load_release(url: str) -> DiscogsRelease:
    parsed = urlsplit(url.strip())
    release_match = None
    if parsed.scheme == "https" and parsed.netloc.lower() in ("discogs.com", "www.discogs.com"):
        release_match = re.fullmatch(r"/release/([1-9][0-9]*)(?:-[^/]+)?/?", parsed.path)
    elif parsed.scheme == "https" and parsed.netloc.lower() == "api.discogs.com":
        release_match = re.fullmatch(r"/releases/([1-9][0-9]*)/?", parsed.path)
    if release_match is None:
        raise DiscogsError("Use an HTTPS Discogs release URL for a specific edition.")

    release_id = release_match[1]
    data = _get_release(release_id)
    release = _parse_release(data, release_id)
    return replace(release, edition=replace(release.edition, dates=_resolve_dates(data.get("master_id"), release.edition)))


def _get_release(release_id: str) -> dict[str, object]:
    req = _request(f"https://api.discogs.com/releases/{release_id}", "application/json")
    data = _json(req, f"release {release_id}")
    if not isinstance(data, dict) or type(data.get("id")) is not int or str(data["id"]) != release_id:
        raise DiscogsError(f"Discogs did not return the requested edition {release_id}.")
    return data


def _parse_release(data: dict[str, object], release_id: str) -> DiscogsRelease:
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DiscogsError(f"Discogs did not provide a title for release {release_id}.")

    tracklist = data.get("tracklist")
    complete = isinstance(tracklist, list) and bool(tracklist)
    tracks: list[EditionTrack] = []
    for index, entry in enumerate(tracklist if isinstance(tracklist, list) else (), 1):
        if isinstance(entry, dict) and entry.get("type_") == "heading":
            continue
        if not isinstance(entry, dict) or entry.get("type_") != "track" or entry.get("sub_tracks"):
            complete = False
            continue
        position, track_title = entry.get("position"), entry.get("title")
        tracks.append(EditionTrack(
            str(index), position.strip() if isinstance(position, str) else "",
            track_title if isinstance(track_title, str) else "", _duration(entry.get("duration")),
            artist_credits=_artist_credits(entry.get("artists")),
        ))

    formats = data.get("formats")
    media = _media_type(formats)
    styles = data.get("styles")
    genre_sources = ()
    if isinstance(styles, list) and styles and all(isinstance(style, str) and style.strip() for style in styles):
        genre_sources = (GenreSource("discogs", release_id, tuple(styles)),)

    released, year = _date_value(data.get("released")), data.get("year")
    if released is None and type(year) is int and 1000 <= year <= 9999:
        released = str(year)
    date_source = DateSource("discogs", release_id, released) if released else None

    edition = Edition(
        "discogs", release_id, media, tuple(tracks), complete and bool(tracks),
        genre_sources=genre_sources, dates=ReleaseDates(edition_date=date_source),
        release_title=title, artist_credits=_artist_credits(data.get("artists")),
        release_type_evidence=_release_type_evidence(formats, data.get("extraartists")),
        disc_count=_disc_count(formats, media),
    )
    return DiscogsRelease(title, edition, _release_images(data.get("images")))


def _date_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"[1-9][0-9]{3}(?:-(?:0[0-9]|1[0-2])(?:-00)?)?", value):
        return value[:4]
    if re.fullmatch(r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return None


def _clearly_earlier(first: str, second: str) -> bool:
    return first[:4] < second[:4] or (len(first) == len(second) == 10 and first < second)


def _original_candidate(master_id: int) -> tuple[str, str] | None:
    url = f"https://api.discogs.com/masters/{master_id}/versions?sort=released&sort_order=asc&per_page=100"
    data = _json(_request(url, "application/json"), f"versions of master {master_id}")
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        return None
    pagination = data.get("pagination")
    if not isinstance(pagination, dict) or pagination.get("page") != 1:
        return None
    pages = pagination.get("pages")
    if type(pages) is not int or pages < 1:
        return None

    candidates: dict[str, str] = {}
    years: set[str] = set()
    for version in data["versions"]:
        if not isinstance(version, dict):
            return None
        release_id, released = version.get("id"), _date_value(version.get("released"))
        if type(release_id) is not int or release_id < 1:
            return None
        if released is None:
            continue
        years.add(released[:4])
        format_labels = {label.strip() for label in _text(version.get("format")).casefold().split(",")}
        if not format_labels & _LATER_OR_NONRETAIL:
            key = str(release_id)
            if key in candidates and candidates[key] != released:
                return None
            candidates[key] = released
    if not candidates:
        return None
    candidate = min(candidates, key=candidates.__getitem__)
    released = candidates[candidate]
    if pages > 1 and max(years) <= released[:4]:
        return None
    if any(other != candidate and not _clearly_earlier(released, other_date)
           for other, other_date in candidates.items()):
        return None
    return candidate, released


def _resolve_dates(master_id: object, edition: Edition) -> ReleaseDates:
    dates = edition.dates or ReleaseDates()
    if type(master_id) is not int or master_id < 1:
        return replace(dates, relationship_reason="Discogs did not link this edition to a master for comparison. Confirm the applicable date.")
    try:
        candidate = _original_candidate(master_id)
        if candidate is None:
            return replace(dates, relationship_reason="Discogs does not identify one earliest dated original edition. Confirm the applicable date.")
        original_id, listed_date = candidate
        original = edition
        if original_id != edition.release_id:
            data = _get_release(original_id)
            if type(data.get("master_id")) is not int or data["master_id"] != master_id:
                return replace(dates, relationship_reason="Discogs does not confirm the comparison edition belongs to the same master.")
            original = _parse_release(data, original_id).edition
    except DiscogsError as exc:
        return replace(dates, relationship_reason=f"Original release lookup was unavailable. {exc}")

    formats = original.release_type_evidence
    if formats and any(label.casefold() in _LATER_OR_NONRETAIL
                       for descriptions in (formats.edition_descriptions, *formats.formats) for label in descriptions):
        return replace(dates, relationship_reason="Discogs format descriptions do not support the earliest listing as an original edition.")
    original_date = original.dates.edition_date if original.dates else None
    if original_date is None or _clearly_earlier(original_date.value, listed_date) or _clearly_earlier(listed_date, original_date.value):
        return replace(dates, relationship_reason="Discogs has missing or conflicting dates for the earliest listed edition.")
    if original_id == edition.release_id:
        return replace(dates, content_relationship="original",
                       relationship_reason="Use the date of the earliest supported Discogs edition.")
    if dates.edition_date and not _clearly_earlier(original_date.value, dates.edition_date.value):
        return replace(dates, original_date=original_date,
                       relationship_reason="Discogs dates do not establish that the comparison edition came first.")
    relationship = compare_release_content(edition, original)
    reason = {
        "unchanged": f"Use the date of Discogs release {original_id}. The complete tracklists support the same recordings and order.",
        "changed": f"Use this edition's date. Its tracklist differs from the original Discogs release {original_id}.",
        "unknown": f"Discogs release {original_id} has insufficient or conflicting recording evidence. Confirm the applicable date.",
    }[relationship]
    return ReleaseDates(dates.edition_date, original_date, relationship, reason)


def _media_type(formats: object) -> str:
    if not isinstance(formats, list) or not formats:
        return ""
    media_types: set[str] = set()
    has_container = False
    for item in formats:
        if not isinstance(item, dict):
            return ""
        name, descriptions = item.get("name"), item.get("descriptions")
        if name in ("Box Set", "All Media"):
            has_container = True
        elif name == "Vinyl" and isinstance(descriptions, list) and "LP" in descriptions:
            media_types.add("LP")
        elif name == "File":
            media_types.add("WEB")
        elif isinstance(name, str) and name in ("Vinyl", "CD", "DVD"):
            media_types.add(name)
        else:
            return ""
    if not media_types or (has_container and not media_types <= DISC_MEDIA):
        return ""
    if media_types <= {"Vinyl", "LP"}:
        return "Vinyl" if "Vinyl" in media_types else "LP"
    return next(iter(media_types)) if len(media_types) == 1 else ""


def _disc_count(formats: object, media: str) -> int | None:
    if media not in DISC_MEDIA or not isinstance(formats, list):
        return None
    count = 0
    for item in formats:
        if not isinstance(item, dict) or item.get("name") != media:
            continue
        quantity = item.get("qty")
        if isinstance(quantity, str) and re.fullmatch(r"[0-9]{1,4}", quantity):
            quantity = int(quantity)
        if type(quantity) is not int or quantity < 1:
            return None
        count += quantity
    return count or None
