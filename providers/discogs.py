"""Search Discogs release editions and load their metadata and artwork."""

from dataclasses import dataclass
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib import request

from core.artist_credits import ArtistCredit
from core.rules_engine import DateSource, Edition, EditionTrack, GenreSource, ReleaseDates


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
    req = _request(f"https://api.discogs.com/releases/{release_id}", "application/json")
    data = _json(req, f"release {release_id}")

    if not isinstance(data, dict) or type(data.get("id")) is not int or str(data["id"]) != release_id:
        raise DiscogsError(f"Discogs did not return the requested edition {release_id}.")
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
    styles = data.get("styles")
    genre_sources = ()
    if isinstance(styles, list) and styles and all(isinstance(style, str) and style.strip() for style in styles):
        genre_sources = (GenreSource("discogs", release_id, tuple(styles)),)

    released, year = data.get("released"), data.get("year")
    date_source = None
    if isinstance(released, str) and re.fullmatch(r"[1-9][0-9]{3}(?:-(?!00)[0-9]{2}-(?!00)[0-9]{2})?", released):
        date_source = DateSource("discogs", release_id, released)
    elif type(year) is int and 1000 <= year <= 9999:
        date_source = DateSource("discogs", release_id, str(year))

    edition = Edition(
        "discogs", release_id, _media_type(formats), tuple(tracks), complete and bool(tracks),
        genre_sources=genre_sources, dates=ReleaseDates(edition_date=date_source),
        release_title=title, artist_credits=_artist_credits(data.get("artists")),
    )
    return DiscogsRelease(title, edition, _release_images(data.get("images")))


def _media_type(formats: object) -> str:
    if not isinstance(formats, list) or not formats:
        return ""
    media_types: set[str] = set()
    for item in formats:
        if not isinstance(item, dict):
            return ""
        name, descriptions = item.get("name"), item.get("descriptions")
        if name == "Vinyl" and isinstance(descriptions, list) and "LP" in descriptions:
            media_types.add("LP")
        elif name == "File":
            media_types.add("WEB")
        elif isinstance(name, str) and name in ("Vinyl", "CD", "DVD"):
            media_types.add(name)
        else:
            return ""
    if media_types <= {"Vinyl", "LP"}:
        return "Vinyl" if "Vinyl" in media_types else "LP"
    return next(iter(media_types)) if len(media_types) == 1 else ""
