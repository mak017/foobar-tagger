"""Apply explicitly approved folder and embedded artwork outputs."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from tempfile import mkstemp
from threading import Lock

from core.cover_engine import PreparedCover, cover_paths, validate_prepared_cover
from core.flac_input import FileState, LocalRelease, directory_identity, file_state
from core.tag_writer import COVER_STAGING_SUFFIX, STAGING_PREFIX, ApplyResult, FieldResult, embed_front_cover


_FOLDER_LOCK = Lock()


@dataclass(frozen=True)
class FolderCoverReview:
    target: Path
    existing: tuple[tuple[Path, FileState], ...]

    @property
    def replaces_target(self) -> bool:
        return any(path == self.target for path, _ in self.existing)

    @property
    def removals(self) -> tuple[tuple[Path, FileState], ...]:
        return tuple((path, state) for path, state in self.existing if path != self.target)


@dataclass(frozen=True)
class ArtworkReview:
    local: LocalRelease
    cover: PreparedCover
    folder: FolderCoverReview | None
    folder_issue: str = ""


def review_artwork(local: LocalRelease, cover: PreparedCover) -> ArtworkReview:
    """Record the exact target and existing cover filenames before approval."""
    validate_prepared_cover(cover)
    try:
        folder = _folder_snapshot(local, cover)
    except (OSError, ValueError) as exc:
        return ArtworkReview(local, cover, None, str(exc))
    return ArtworkReview(local, cover, folder)


def _check_directory(local: LocalRelease) -> None:
    if local.root.resolve(strict=True) != local.root or directory_identity(local.root) != local.root_identity:
        raise ValueError("The release folder changed after inspection. Refresh the review.")


def _folder_snapshot(local: LocalRelease, cover: PreparedCover) -> FolderCoverReview:
    _check_directory(local)
    target, paths = cover_paths(local.root, cover)
    return FolderCoverReview(target, tuple((path, file_state(path)) for path in paths))


def apply_artwork(review: ArtworkReview, *, save_folder: bool, embed: bool) -> ApplyResult:
    """Apply only the selected outputs from an artwork review."""
    if not save_folder and not embed:
        return ApplyResult((), (), ())
    validate_prepared_cover(review.cover)
    folder_result = ApplyResult((), (), ())
    if save_folder:
        with _FOLDER_LOCK:
            folder_result = _apply_folder(review)
    embedded_result = embed_front_cover(review.local, review.cover) if embed else ApplyResult((), (), ())
    return ApplyResult(folder_result.fields + embedded_result.fields, (), folder_result.warnings + embedded_result.warnings)


def _apply_folder(review: ArtworkReview) -> ApplyResult:
    folder = review.folder
    if folder is None:
        target = review.local.root / review.cover.filename
        return ApplyResult((FieldResult(str(target), "Folder cover", "failed", review.folder_issue),), (), ())
    warnings: list[str] = []

    def check_review() -> None:
        if _folder_snapshot(review.local, review.cover) != folder:
            raise ValueError("Folder covers changed after inspection. Refresh the artwork review.")

    try:
        check_review()
        if not os.access(review.local.root, os.W_OK) or any(not os.access(path, os.W_OK) for path, _ in folder.existing):
            raise PermissionError("Write permission is required for the release folder and reviewed covers.")
        if shutil.disk_usage(review.local.root).free < len(review.cover.data):
            raise OSError("Insufficient space to stage the folder cover.")
        fd, name = mkstemp(prefix=STAGING_PREFIX, suffix=COVER_STAGING_SUFFIX, dir=review.local.root)
        staged = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(review.cover.data)
                stream.flush()
                os.fsync(stream.fileno())
            if staged.read_bytes() != review.cover.data:
                raise ValueError("The staged folder cover does not match the reviewed image.")
            check_review()
            os.replace(staged, folder.target)
        finally:
            try:
                staged.unlink(missing_ok=True)
            except OSError as exc:
                warnings.append(f"Could not remove temporary file {staged}: {exc}")
    except (OSError, ValueError) as exc:
        failed = FieldResult(str(folder.target), "Folder cover", "failed", str(exc))
        skipped = tuple(FieldResult(str(path), "Remove alternate cover", "skipped", "The new cover was not installed.")
                        for path, _ in folder.removals)
        return ApplyResult((failed, *skipped), (), tuple(warnings))

    fields = [FieldResult(str(folder.target), "Folder cover", "changed", "Verified and installed the approved cover.")]
    for path, expected in folder.removals:
        try:
            _check_directory(review.local)
            if file_state(path) != expected:
                raise ValueError("The alternate changed after review and was left in place.")
            path.unlink()
        except FileNotFoundError:
            fields.append(FieldResult(str(path), "Remove alternate cover", "skipped", "The alternate is already absent."))
        except (OSError, ValueError) as exc:
            fields.append(FieldResult(str(path), "Remove alternate cover", "failed", str(exc)))
            warnings.append(f"Review the remaining alternate cover at {path} before retrying.")
        else:
            fields.append(FieldResult(str(path), "Remove alternate cover", "changed", "Removed the approved alternate cover."))
    return ApplyResult(tuple(fields), (), tuple(warnings))
