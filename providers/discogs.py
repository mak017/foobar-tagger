"""Load a specific Discogs edition from a release URL."""

from dataclasses import dataclass
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib import request

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
    try:
        with request.urlopen(req, timeout=10) as response:
            data = json.load(response)
    except HTTPError as exc:
        exc.close()
        raise DiscogsError(f"Discogs returned HTTP {exc.code} for release {release_id}. Check the URL or retry.") from exc
    except (URLError, OSError) as exc:
        raise DiscogsError(f"Could not reach Discogs for release {release_id}: {exc}. Retry the lookup.") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise DiscogsError(f"Discogs returned invalid JSON for release {release_id}. Retry the lookup.") from exc

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
            track_title if isinstance(track_title, str) else "",
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
