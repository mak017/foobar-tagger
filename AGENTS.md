# Repository Guidelines

## Project Structure & Module Organization

This repository currently contains only `PLAN.md`, the architecture and implementation specification for the foobar2000 Auto-Tagger & Cover-Art Engine. Consult it before implementing metadata rules or provider behavior.

The planned Python layout is:

- `main.py`: CLI entry point and multi-process queue aggregation.
- `config.py`: configuration, thresholds, and tag mappings.
- `providers/`: Apple Music, Beatport, Discogs, Deezer, and Bandcamp integrations.
- `core/`: clue parsing, normalization rules, artwork processing, and Mutagen tag writing.
- `ui/`: Tkinter review window, metadata diff, and artwork preview.
- `Tag_Selection.bat`: Windows launcher for foobar2000 integration.

These files and directories are not implemented yet. Add tests under `tests/`; keep small synthetic fixtures there rather than committing music libraries or downloaded artwork.

## Build, Test, and Development Commands

No build system, dependency manifest, runnable entry point, or test runner is configured yet. Do not assume documented architecture paths are executable.

For initial Python development, create an isolated environment with `python -m venv .venv` and activate it in PowerShell using `.\.venv\Scripts\Activate.ps1`. When adding runnable code, document dependency installation, launch commands, and the test command alongside its configuration.

## Coding Style & Naming Conventions

Use four-space indentation for Python, `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep provider access separate from normalization rules and file writes. Preserve exact tag names and release-type values defined in `PLAN.md`. No formatter or linter is configured.

## Testing Guidelines

No testing framework or coverage threshold is established. Add focused tests for folder parsing, original-release dates, conditional album artists, vinyl numbering, and release classification. Name test modules `test_<feature>.py`. Mock provider responses and use temporary copies for tag-writing tests. Verify artwork is at least 1000 × 1000 pixels and strictly below 5 MB.

## Commit & Pull Request Guidelines

This checkout has no Git metadata, so no existing commit convention can be inferred. Use concise imperative subjects, such as `Add Discogs vinyl track mapping`. Describe behavior changes, reference relevant plan sections or issues, and report validation performed. Include screenshots for UI changes.

## Configuration & Data Safety

Keep API credentials outside tracked files. Preserve the planned review-before-apply workflow, and validate writes using disposable audio fixtures before touching a personal library.