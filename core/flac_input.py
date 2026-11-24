"""Inspect a proposed local release and distinguish selection from context."""

from dataclasses import dataclass
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Mapping

from mutagen import MutagenError
from mutagen.flac import FLAC

from core.rules_engine import LocalFile


FLAC_FIELDS = {
    "%ARTIST%": ("ARTIST",),
    "%ALBUM%": ("ALBUM",),
    "%ALBUM ARTIST%": ("ALBUMARTIST", "ALBUM ARTIST"),
    "%TITLE%": ("TITLE",),
    "%DATE%": ("DATE",),
    "%RELEASE DATE%": ("RELEASE DATE",),
    "%GENRE%": ("GENRE",),
    "%RELEASETYPE%": ("RELEASETYPE",),
    "%TRACKNUMBER%": ("TRACKNUMBER",),
    "%TOTALTRACKS%": ("TOTALTRACKS", "TRACKTOTAL"),
    "%DISCNUMBER%": ("DISCNUMBER",),
    "%TOTALDISCS%": ("TOTALDISCS", "DISCTOTAL"),
    "%VINYLSIDE%": ("VINYLSIDE",),
    "%VINYLTRACKNUMBER%": ("VINYLTRACKNUMBER",),
}


@dataclass(frozen=True)
class FileState:
    device: int
    identity: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int


def file_state(path: Path) -> FileState:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    return FileState(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def directory_identity(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or path.is_junction():
        raise ValueError(f"Expected a release directory: {path}")
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class LocalRelease:
    root: Path
    files: tuple[LocalFile, ...]
    selected_file_ids: frozenset[str]
    warnings: tuple[str, ...]
    file_states: Mapping[str, FileState]
    root_identity: tuple[int, int]


def _disc_folder(path: Path) -> bool:
    return path.is_dir() and bool(re.fullmatch(
        r"(?:cd|dvd|disc|disk|record|lp)[ _-]*[0-9]+(?:\s+.+)?",
        path.name, flags=re.IGNORECASE,
    ))


def _read_file(path: Path, root: Path) -> tuple[LocalFile, FileState]:
    file_id = path.relative_to(root).as_posix()
    try:
        before = file_state(path)
        audio = FLAC(str(path))
        if before != file_state(path):
            raise ValueError(f"{file_id} changed during inspection. Refresh the review.")
    except (MutagenError, OSError) as exc:
        raise ValueError(f"Could not inspect {file_id}: {exc}") from exc

    tags: dict[str, tuple[str, ...]] = {}
    if audio.tags is not None:
        for field, keys in FLAC_FIELDS.items():
            present = [tuple(audio[key]) for key in keys if key in audio]
            if present and all(values == present[0] for values in present):
                tags[field] = present[0]
            elif present:
                tags[field] = tuple(value for values in present for value in values)
    duration = audio.info.length
    return LocalFile(file_id, tags, duration if duration > 0 else None), before


def inspect_release(paths: list[Path], release_folder: Path | None = None) -> LocalRelease:
    """Read direct FLACs and FLACs in immediate disc subfolders."""
    if not paths:
        raise ValueError("Supply a release folder or selected FLAC paths.")
    paths = [path.resolve(strict=True) for path in paths]
    folder_input = len(paths) == 1 and paths[0].is_dir()
    if folder_input:
        if release_folder is not None:
            raise ValueError("Use --release-folder with selected file paths only.")
        root = paths[0]
    else:
        if any(not path.is_file() or path.suffix.lower() != ".flac" for path in paths):
            raise ValueError("Supply one folder or a list of FLAC files.")
        if release_folder is not None:
            root = release_folder.resolve(strict=True)
        else:
            parents = {path.parent.parent if _disc_folder(path.parent) else path.parent for path in paths}
            if len(parents) != 1:
                raise ValueError("Selected files span release folders. Select one release or specify --release-folder.")
            root = parents.pop()
    if not root.is_dir():
        raise ValueError(f"The release folder is not a directory: {root}")
    root_identity = directory_identity(root)

    warnings: list[str] = []
    candidates: set[Path] = set()
    entries = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    for entry in entries:
        if entry.is_symlink() or entry.is_junction():
            continue
        if entry.is_file() and entry.suffix.lower() == ".flac":
            candidates.add(entry)
        elif _disc_folder(entry):
            candidates.update(
                path for path in entry.iterdir()
                if not path.is_symlink() and path.is_file() and path.suffix.lower() == ".flac"
            )
        elif entry.is_dir():
            warnings.append(f"Folder outside the proposed release: {entry.name}")

    if not candidates:
        raise ValueError(f"No FLAC files found in {root} or its immediate disc subfolders.")
    selected = candidates if folder_input else set(paths)
    if not selected <= candidates:
        outside = ", ".join(str(path) for path in sorted(selected - candidates))
        raise ValueError(f"Selected files outside the proposed release: {outside}")

    inspected = tuple(_read_file(path, root) for path in sorted(candidates))
    if directory_identity(root) != root_identity:
        raise ValueError("The release folder changed during inspection. Refresh the review.")
    files = tuple(file for file, _ in inspected)
    for field in ("%ALBUM%", "%ALBUM ARTIST%"):
        values = {
            value.strip().casefold() for file in files for value in file.tags.get(field, ())
            if value.strip()
        }
        if len(values) > 1:
            warnings.append(f"Possible unrelated files: existing {field} values differ across the proposed release.")
    return LocalRelease(
        root, files, frozenset(path.relative_to(root).as_posix() for path in selected), tuple(warnings),
        MappingProxyType({file.file_id: state for file, state in inspected}), root_identity,
    )
