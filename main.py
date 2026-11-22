"""Inspect one FLAC release in the terminal or open the metadata review window."""

import argparse
import os
from pathlib import Path
import sys

from core.clue_parser import parse_folder_clues
from core.flac_input import LocalRelease, inspect_release
from core.rules_engine import Edition, MetadataPolicy, ReleaseContext, propose_metadata
from providers.discogs import load_release


def _track_mapping(
    entries: list[list[str]], local: LocalRelease, edition: Edition,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    selected = {os.path.normcase(file_id): file_id for file_id in local.selected_file_ids}
    for file_name, position in entries:
        file_id = selected.get(os.path.normcase(file_name))
        if file_id is None:
            raise ValueError(f"Mapping must name a selected file relative to the release folder: {file_name}")
        if file_id in mapping:
            raise ValueError(f"Multiple mappings supplied for {file_id}.")
        matches = [track for track in edition.tracks if track.position and track.position.casefold() == position.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Position {position!r} does not identify exactly one track in this edition.")
        track = matches[0]
        if track.track_id in mapping.values():
            raise ValueError(f"Position {position!r} is mapped to more than one selected file.")
        mapping[file_id] = track.track_id
    return mapping


def _show_values(values: tuple[str, ...] | None) -> str:
    if values is None:
        return "(absent)"
    return " | ".join(value or "(empty)" for value in values) or "(empty)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*", help="one release folder or selected FLAC paths")
    parser.add_argument("--review", action="store_true", help="open the interactive Tkinter review window")
    parser.add_argument("--release-folder", type=Path, help="override the inferred root when selecting files")
    parser.add_argument("--confirm-release", action="store_true", help="confirm the proposed local release membership")
    parser.add_argument("--discogs-url", help="Discogs URL for the edition to review")
    parser.add_argument("--map", nargs=2, action="append", default=[], metavar=("FILE", "POSITION"),
                        help="confirm a selected relative file path and its Discogs position (repeatable)")
    parser.add_argument("--accept-style", action="append", default=[], metavar="STYLE",
                        help="admit a Discogs style to this preview's vocabulary (repeatable)")
    args = parser.parse_args(argv)

    if args.review:
        if args.confirm_release or args.map:
            parser.error("Confirm grouping and map files in the review window when using --review.")
        from ui.app import run_review

        return run_review(args.paths, args.release_folder, args.discogs_url or "", frozenset(args.accept_style))
    if not args.paths:
        parser.error("Supply a release folder or selected FLAC paths, or use --review.")

    try:
        local = inspect_release(args.paths, args.release_folder)
        print(f"Release folder: {local.root}")
        clues = parse_folder_clues(local.root.name)
        print("Folder clues:")
        for label, value in (
            ("Catalog", clues.catalog), ("Artist", clues.artist), ("Album", clues.album),
            ("Year", clues.year), ("Media", clues.media_type),
        ):
            if value:
                print(f"  {label}: {value}")
        for file in local.files:
            status = "Selected" if file.file_id in local.selected_file_ids else "Context only"
            details = ", ".join(
                f"{label}: {_show_values(file.tags[field])}"
                for field, label in (("%TITLE%", "title"), ("%ALBUM%", "album")) if field in file.tags
            )
            print(f"{status}: {file.file_id}" + (f" ({details})" if details else ""))
        for warning in local.warnings:
            print(f"Warning: {warning}")
        if not args.confirm_release:
            print("Confirm the grouping with --confirm-release and supply --discogs-url to preview metadata.")
            return 0
        if not args.discogs_url:
            raise ValueError("Supply --discogs-url with --confirm-release to load the edition.")

        release = load_release(args.discogs_url)
        edition = release.edition
        print(f"Discogs edition {edition.release_id}: {release.title} ({edition.media_type or 'media unresolved'})")
        if clues.media_type and edition.media_type and clues.media_type != edition.media_type:
            print(f"Warning: Folder media clue {clues.media_type} differs from Discogs media {edition.media_type}.")
        if clues.album and clues.album.casefold() != release.title.strip().casefold():
            print(f"Warning: Folder album clue {clues.album!r} differs from the Discogs title.")
        if not edition.tracklist_complete:
            print("Warning: Discogs did not provide a supported complete tracklist.")
        for track in edition.tracks:
            print(f"  {track.position or '(unpositioned)'}: {track.title or '(untitled)'}")

        mapping = _track_mapping(args.map, local, edition)
        track_positions = {track.track_id: track.position for track in edition.tracks}
        for file in local.files:
            if file.file_id in local.selected_file_ids:
                track_id = mapping.get(file.file_id)
                print(f"Mapping: {file.file_id} -> {track_positions[track_id] if track_id else '(unresolved)'}")

        proposal = propose_metadata(
            ReleaseContext(local.files, local.selected_file_ids, edition, mapping, membership_complete=True),
            MetadataPolicy(accepted_styles=frozenset(args.accept_style)),
        )
        print("Read-only metadata proposal:")
        for decision in proposal.decisions:
            if decision.state == "KEEP":
                continue
            values = _show_values(decision.existing_values)
            if decision.state in ("SET", "DELETE"):
                values += f" -> {_show_values(decision.proposed_values)}"
            evidence = decision.evidence
            print(f"  {decision.file_id} {decision.field} {decision.state}: {values}")
            print(f"    {decision.reason} [{evidence.provider} {evidence.release_id}, source: {evidence.original_value!r}]")
        for issue in proposal.issues:
            print(f"  {issue.code}: {issue.message} ({', '.join(issue.file_ids)})")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
