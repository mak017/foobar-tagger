"""Apply explicitly approved FLAC metadata decisions to a reviewed selection."""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from tempfile import mkstemp
from threading import Lock
from typing import Literal

from mutagen import MutagenError
from mutagen.flac import FLAC, Picture, VCFLACDict

from core.cover_engine import FRONT_COVER_TYPE, PreparedCover, validate_prepared_cover
from core.flac_input import FLAC_FIELDS, LocalRelease, directory_identity, file_state, inspect_release
from core.rules_engine import FieldDecision, MetadataProposal


DecisionKey = tuple[str, str]
RELEASE_FIELDS = {"%DATE%": ("%DATE%", "%RELEASE DATE%"), "%GENRE%": ("%GENRE%",)}
STAGING_PREFIX = ".foobar-tagger-"
STAGING_SUFFIX = ".flac.tmp"
COVER_STAGING_SUFFIX = ".cover.tmp"
_APPLICATION_LOCK = Lock()


class InvalidApproval(ValueError):
    """The approved decisions do not form an applicable subset of the proposal."""


@dataclass(frozen=True)
class FieldResult:
    file_id: str
    field: str
    status: Literal["changed", "failed", "skipped"]
    reason: str


@dataclass(frozen=True)
class ApplyResult:
    fields: tuple[FieldResult, ...]
    inconsistent_fields: tuple[str, ...]
    warnings: tuple[str, ...]


def _validate_approval(
    local: LocalRelease, proposal: MetadataProposal, approved: Collection[FieldDecision],
) -> tuple[dict[DecisionKey, FieldDecision], dict[str, frozenset[DecisionKey]]]:
    reviewed = {(decision.file_id, decision.field): decision for decision in proposal.decisions}
    if len(reviewed) != len(proposal.decisions):
        raise InvalidApproval("The proposal contains duplicate file and field decisions.")
    files = {file.file_id: file for file in local.files}
    accepted: dict[DecisionKey, FieldDecision] = {}
    for decision in approved:
        key = (decision.file_id, decision.field)
        file = files.get(decision.file_id)
        if file is None or decision.file_id not in local.selected_file_ids:
            raise InvalidApproval(f"Approved file is outside the reviewed selection: {decision.file_id}")
        if reviewed.get(key) != decision or file.tags.get(decision.field) != decision.existing_values:
            raise InvalidApproval(f"The approval does not match the reviewed values for {key}.")
        if decision.field not in FLAC_FIELDS or not (
            (decision.state == "SET" and decision.proposed_values and all(decision.proposed_values))
            or (decision.state == "DELETE" and decision.existing_values is not None and decision.proposed_values is None)
        ):
            raise InvalidApproval(f"There is no applicable change for {key}.")
        accepted[key] = decision

    groups: dict[str, frozenset[DecisionKey]] = {}
    for group in proposal.approval_groups:
        members = group.changes
        keys = frozenset((file_id, field) for file_id, field, _ in members)
        if not keys.intersection(accepted):
            continue
        if not keys.issubset(accepted) or any(
            accepted[file_id, field].proposed_values != values for file_id, field, values in members
        ):
            raise InvalidApproval(f"Approve the complete linked changes for {group.field}, or leave them all unchecked.")
        if group.field in RELEASE_FIELDS:
            groups[group.field] = keys

    for group_field, fields in RELEASE_FIELDS.items():
        if any(key[1] in fields and key not in groups.get(group_field, ()) for key in accepted):
            raise InvalidApproval(f"Changes to {group_field} require a reviewed release-wide approval group.")
    return accepted, groups


def _reviewed_path(local: LocalRelease, file_id: str) -> Path:
    relative = Path(file_id)
    path = local.root / relative
    expected = local.file_states.get(file_id)
    if relative.anchor or ".." in relative.parts or path.suffix.lower() != ".flac" or expected is None:
        raise ValueError(f"Invalid reviewed FLAC path: {file_id}")
    try:
        unchanged = (directory_identity(local.root) == local.root_identity
                     and path.resolve(strict=True) == path and file_state(path) == expected)
    except FileNotFoundError:
        unchanged = False
    if not unchanged:
        raise ValueError(f"{file_id} changed after inspection. Refresh the review.")
    return path


def _preflight(local: LocalRelease, file_id: str, added_bytes: int) -> Path:
    path = _reviewed_path(local, file_id)
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        raise OSError(f"Write permission is required for {file_id} and its directory.")
    if shutil.disk_usage(path.parent).free < local.file_states[file_id].size + added_bytes:
        raise OSError(f"Insufficient space for a temporary copy of {file_id}.")
    return path


def _is_front_cover(block: object) -> bool:
    return isinstance(block, Picture) and block.type == FRONT_COVER_TYPE


def _unmanaged_blocks(audio: FLAC, replace_front: bool) -> tuple[tuple[int, bytes], ...]:
    return tuple((block.code, block.write()) for block in audio.metadata_blocks
                 if block.code not in (1, 4) and not (replace_front and _is_front_cover(block)))


def _write_file(
    local: LocalRelease, path: Path, file_id: str, changes: Collection[FieldDecision], warnings: list[str],
    *, front_cover: Picture | None = None,
) -> bool:
    _reviewed_path(local, file_id)
    fd, name = mkstemp(prefix=STAGING_PREFIX, suffix=STAGING_SUFFIX, dir=path.parent)
    staged = Path(name)
    try:
        os.close(fd)
        shutil.copy2(path, staged)
        audio = FLAC(str(staged))
        if sum(block.code == 4 for block in audio.metadata_blocks) > 1:
            raise ValueError("Multiple FLAC comment blocks require manual review.")
        replace_front = front_cover is not None
        blocks = _unmanaged_blocks(audio, replace_front)
        expected_front = ()
        if front_cover is not None:
            expected_front = (front_cover.write(),)
            front = [picture for picture in audio.pictures if _is_front_cover(picture)]
            if not changes and len(front) == 1 and front[0].data == front_cover.data and front[0].mime == front_cover.mime:
                return False
            audio.metadata_blocks[:] = [block for block in audio.metadata_blocks if not _is_front_cover(block)]
            audio.add_picture(front_cover)
        vendor = audio.tags.vendor if isinstance(audio.tags, VCFLACDict) else None
        expected_tags = {key: tuple(values) for key, values in audio.items()}
        for decision in changes:
            canonical, *aliases = FLAC_FIELDS[decision.field]
            for key in (canonical, *aliases):
                expected_tags.pop(key.lower(), None)
                if key in audio:
                    del audio[key]
            if decision.proposed_values is not None:
                audio[canonical] = list(decision.proposed_values)
                expected_tags[canonical.lower()] = decision.proposed_values
        audio.save(padding=lambda _: 0)

        verified = FLAC(str(staged))
        verified_tags = {key: tuple(values) for key, values in verified.items()}
        verified_vendor = verified.tags.vendor if isinstance(verified.tags, VCFLACDict) else None
        if replace_front and tuple(picture.write() for picture in verified.pictures if _is_front_cover(picture)) != expected_front:
            raise ValueError("The staged FLAC front cover did not verify. Refresh the review before retrying.")
        if expected_tags != verified_tags or blocks != _unmanaged_blocks(verified, replace_front) or (
            vendor is not None and verified_vendor != vendor
        ):
            raise ValueError("The staged FLAC did not verify. Refresh the review before retrying.")
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        _reviewed_path(local, file_id)
        os.replace(staged, path)
        return True
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"Could not remove temporary file {staged}: {exc}")


def _inconsistent_fields(
    local: LocalRelease, fields: Collection[str], projected: dict[DecisionKey, FieldDecision],
) -> tuple[str, ...]:
    inconsistent: list[str] = []
    for field in fields:
        values: set[tuple[str, ...] | None] = set()
        for file in local.files:
            decision = projected.get((file.file_id, field))
            values.add(decision.proposed_values if decision is not None else file.tags.get(field))
        if len(values) > 1:
            inconsistent.append(field)
    return tuple(inconsistent)


def apply_metadata(
    local: LocalRelease, proposal: MetadataProposal, approved: Collection[FieldDecision],
) -> ApplyResult:
    """Write the exact approved changes to freshly verified FLAC copies."""
    accepted, groups = _validate_approval(local, proposal, approved)
    with _APPLICATION_LOCK:
        return _apply(local, proposal, accepted, groups)


def embed_front_cover(local: LocalRelease, cover: PreparedCover) -> ApplyResult:
    """Apply an approved front cover through the same staged FLAC writer."""
    validate_prepared_cover(cover)
    picture = Picture()
    picture.type = FRONT_COVER_TYPE
    picture.mime = "image/png" if cover.info.format == "PNG" else "image/jpeg"
    picture.width, picture.height = cover.info.width, cover.info.height
    picture.data = cover.data
    fields: list[FieldResult] = []
    warnings: list[str] = []
    with _APPLICATION_LOCK:
        for file_id in sorted(local.selected_file_ids):
            try:
                path = _preflight(local, file_id, len(cover.data) + 64)
                changed = _write_file(local, path, file_id, (), warnings, front_cover=picture)
            except (MutagenError, OSError, ValueError) as exc:
                fields.append(FieldResult(file_id, "Embedded front cover", "failed", str(exc)))
            else:
                fields.append(FieldResult(file_id, "Embedded front cover", "changed" if changed else "skipped",
                                          "Verified and replaced the FLAC." if changed else "This front cover is already embedded."))
    return ApplyResult(tuple(fields), (), tuple(warnings))


def _apply(
    local: LocalRelease, proposal: MetadataProposal, accepted: dict[DecisionKey, FieldDecision],
    groups: dict[str, frozenset[DecisionKey]],
) -> ApplyResult:
    results: dict[DecisionKey, FieldResult] = {}
    warnings: list[str] = []
    candidates: dict[str, list[FieldDecision]] = defaultdict(list)
    group_errors: dict[str, str] = {}
    group_by_key = {key: field for field, keys in groups.items() for key in keys}
    release_groups = {field: RELEASE_FIELDS[field] for field in groups if field in RELEASE_FIELDS}
    for decision in proposal.decisions:
        key = (decision.file_id, decision.field)
        if key in accepted:
            candidates[decision.file_id].append(decision)
        elif decision.state != "KEEP":
            reason = decision.reason if decision.state == "UNRESOLVED" else "The change was not approved."
            results[key] = FieldResult(*key, "skipped", reason)

    paths: dict[str, Path] = {}
    for file_id, changes in candidates.items():
        try:
            added_bytes = sum(
                len(FLAC_FIELDS[decision.field][0]) + len(value.encode("utf-8")) + 5
                for decision in changes for value in decision.proposed_values or ()
            )
            paths[file_id] = _preflight(local, file_id, added_bytes)
        except (OSError, ValueError) as exc:
            for decision in changes:
                key = (file_id, decision.field)
                results[key] = FieldResult(*key, "failed", str(exc))
                if key in group_by_key:
                    group_errors[group_by_key[key]] = f"A linked change for {file_id} failed. Refresh the review."

    if release_groups:
        try:
            current = inspect_release([local.root])
            if current.file_states.keys() != local.file_states.keys():
                raise ValueError("Release membership changed after inspection. Refresh the review.")
            for field, related in release_groups.items():
                if _inconsistent_fields(current, related, accepted):
                    group_errors[field] = "The current release would have inconsistent values. Refresh the review."
        except (OSError, ValueError) as exc:
            for field in release_groups:
                group_errors[field] = str(exc)

    changed_release_fields: set[str] = set()
    for file_id, path in paths.items():
        changes: list[FieldDecision] = []
        for decision in candidates[file_id]:
            key = (file_id, decision.field)
            reason = group_errors.get(group_by_key.get(key, ""))
            if reason is None:
                changes.append(decision)
            else:
                results[key] = FieldResult(*key, "skipped", reason)
        if not changes:
            continue
        try:
            _write_file(local, path, file_id, changes, warnings)
        except (MutagenError, OSError, ValueError) as exc:
            for decision in changes:
                key = (file_id, decision.field)
                results[key] = FieldResult(*key, "failed", str(exc))
                if key in group_by_key:
                    group_errors[group_by_key[key]] = f"A linked change for {file_id} failed. Refresh the review."
        else:
            for decision in changes:
                key = (file_id, decision.field)
                results[key] = FieldResult(*key, "changed", "Verified and replaced the FLAC.")
                changed_release_fields.update(release_groups.get(group_by_key.get(key, ""), ()))

    inconsistent = ()
    if changed_release_fields:
        try:
            current = inspect_release([local.root])
            if current.file_states.keys() != local.file_states.keys():
                warnings.append("Release membership changed during application. Refresh the review.")
            inconsistent = _inconsistent_fields(current, sorted(changed_release_fields), {})
            if inconsistent:
                warnings.append("Release-wide tags are inconsistent after application. Refresh the review before retrying.")
        except (OSError, ValueError) as exc:
            warnings.append(f"Could not verify release-wide consistency after application: {exc}")
    keys = ((decision.file_id, decision.field) for decision in proposal.decisions)
    return ApplyResult(tuple(results[key] for key in keys if key in results), inconsistent, tuple(warnings))


def find_staged_files(local: LocalRelease) -> tuple[Path, ...]:
    """List staging copies to inspect after an interrupted application."""
    directories = {(local.root / file.file_id).parent for file in local.files}
    directories.add(local.root)
    return tuple(sorted(
        path for directory in directories
        if directory.is_relative_to(local.root) and directory.resolve() == directory
        for path in directory.glob(f"{STAGING_PREFIX}*.tmp")
        if path.name.endswith((STAGING_SUFFIX, COVER_STAGING_SUFFIX)) and not path.is_symlink() and path.is_file()
    ))
