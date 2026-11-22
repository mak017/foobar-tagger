"""Inspect existing artwork and prepare validated PNG or JPEG cover candidates."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC
from PIL import Image, ImageOps

from core.flac_input import LocalRelease


MIN_COVER_DIMENSION = 1000
MAX_COVER_BYTES = 5_000_000
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "AVIF")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"})


class ArtworkError(ValueError):
    """Artwork cannot be read or prepared within the cover requirements."""


@dataclass(frozen=True)
class ArtworkInfo:
    format: str
    width: int
    height: int
    byte_count: int

    @property
    def meets_output_requirements(self) -> bool:
        return (self.format in ("PNG", "JPEG") and min(self.width, self.height) >= MIN_COVER_DIMENSION
                and 0 < self.byte_count < MAX_COVER_BYTES)


@dataclass(frozen=True)
class PreparedCover:
    data: bytes
    info: ArtworkInfo
    filename: str
    change: str


@dataclass(frozen=True)
class ExistingArtwork:
    label: str
    path: Path
    picture_index: int | None = None

    def read(self) -> bytes:
        try:
            if self.picture_index is None:
                return self.path.read_bytes()
            return FLAC(str(self.path)).pictures[self.picture_index].data
        except (IndexError, MutagenError, OSError) as exc:
            raise ArtworkError(f"Could not read {self.label}: {exc}") from exc


def existing_artwork(local: LocalRelease) -> tuple[tuple[ExistingArtwork, ...], tuple[str, ...]]:
    """List folder images and artwork embedded in selected FLACs."""
    sources = [
        ExistingArtwork(f"Folder · {path.name}", path)
        for path in sorted(local.root.iterdir())
        if path.suffix.lower() in IMAGE_SUFFIXES and not path.is_symlink() and path.is_file()
    ]
    warnings: list[str] = []
    for file_id in sorted(local.selected_file_ids):
        path = local.root / file_id
        try:
            pictures = FLAC(str(path)).pictures
        except (MutagenError, OSError) as exc:
            warnings.append(f"Could not inspect embedded artwork in {file_id}: {exc}")
            continue
        for index, picture in enumerate(pictures):
            kind = "front cover" if picture.type == 3 else "image"
            sources.append(ExistingArtwork(f"Embedded · {file_id} · {kind} {index + 1}", path, index))
    return tuple(sources), tuple(warnings)


def _decode(data: bytes) -> tuple[ArtworkInfo, Image.Image, bool]:
    try:
        with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as source:
            if Image.MAX_IMAGE_PIXELS and source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ArtworkError("The image exceeds the decoder's pixel limit.")
            if getattr(source, "is_animated", False):
                raise ArtworkError("Select a still image for cover artwork.")
            source.load()
            orientation = source.getexif().get(274, 1)
            image = ImageOps.exif_transpose(source)
            return ArtworkInfo(source.format or "Unknown", image.width, image.height, len(data)), image, orientation in range(2, 9)
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ArtworkError(f"Could not decode the artwork: {exc}") from exc


def inspect_artwork(data: bytes) -> ArtworkInfo:
    info, image, _ = _decode(data)
    image.close()
    return info


def preview_image(data: bytes, size: tuple[int, int] = (310, 310)) -> tuple[ArtworkInfo, Image.Image]:
    """Return a display thumbnail, leaving the candidate bytes untouched."""
    info, image, _ = _decode(data)
    with image:
        preview = image.convert("RGBA")
    preview.thumbnail(size, Image.Resampling.LANCZOS)
    return info, preview


def cover_paths(root: Path, cover: PreparedCover) -> tuple[Path, tuple[Path, ...]]:
    """Identify the output path and existing cover-name files in the release folder."""
    destination = root / cover.filename
    existing = tuple(path for path in sorted(root.iterdir())
                     if path.stem.lower() == "cover" and path.suffix.lower() in IMAGE_SUFFIXES and path.is_file())
    return destination, existing


def _encode(image: Image.Image, format_name: str) -> bytes:
    output = BytesIO()
    if format_name == "JPEG":
        image.save(output, format="JPEG", quality=92, optimize=True)
    else:
        image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _prepared(data: bytes, change: str) -> PreparedCover:
    info = inspect_artwork(data)
    if not info.meets_output_requirements:
        raise ArtworkError("The prepared cover does not meet the output requirements.")
    return PreparedCover(data, info, "cover.png" if info.format == "PNG" else "cover.jpg", change)


def prepare_cover(data: bytes) -> PreparedCover:
    """Prepare a candidate without enlarging either dimension or cropping."""
    source, image, rotated = _decode(data)
    with image:
        short_edge = min(source.width, source.height)
        if short_edge < MIN_COVER_DIMENSION:
            raise ArtworkError(f"Both dimensions must be at least {MIN_COVER_DIMENSION} px. The image is {source.width} × {source.height} px.")
        if source.meets_output_requirements and not rotated:
            return PreparedCover(data, source, "cover.png" if source.format == "PNG" else "cover.jpg", "Original bytes")

        has_alpha = "A" in image.getbands() or "transparency" in image.info
        if source.format != "JPEG":
            png = _encode(image, "PNG")
            if len(png) < MAX_COVER_BYTES:
                return _prepared(png, "Lossless PNG re-encode")

        format_name = "PNG" if has_alpha else "JPEG"
        with image.convert("RGBA" if has_alpha else "RGB") as working:
            if format_name == "JPEG":
                jpeg = _encode(working, "JPEG")
                if len(jpeg) < MAX_COVER_BYTES:
                    return _prepared(jpeg, "JPEG re-encode")
            target = short_edge
            while target > MIN_COVER_DIMENSION:
                target = max(MIN_COVER_DIMENSION, int(target * 0.8))
                size = (round(source.width * target / short_edge), round(source.height * target / short_edge))
                with working.resize(size, Image.Resampling.LANCZOS) as smaller:
                    encoded = _encode(smaller, format_name)
                if len(encoded) < MAX_COVER_BYTES:
                    return _prepared(encoded, f"Downscaled and encoded as {format_name}")
    raise ArtworkError(f"No encoding fits under {MAX_COVER_BYTES:,} bytes at the required dimensions.")
