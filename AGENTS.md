# Repository Guidelines

## Project Structure & Module Organization

`PLAN.md` specifies behavior and `DESIGN.md` records the approved proposal interface. Consult them before implementing metadata rules or provider behavior. Current code is in `core/`, with tests under `tests/`; see `README.md` for implemented scope.

The planned Python layout is:

- `main.py`: CLI entry point and multi-process queue aggregation.
- `config.py`: configuration, thresholds, and tag mappings.
- `providers/`: Apple Music, Beatport, Discogs, Deezer, and Bandcamp integrations.
- `core/`: clue parsing, normalization rules, artwork processing, and Mutagen tag writing.
- `ui/`: Tkinter review window, metadata diff, and artwork preview.
- `Tag_Selection.bat`: Windows launcher for foobar2000 integration.

The layout above includes future modules; check the filesystem before assuming a path is implemented. Keep small synthetic fixtures under `tests/` rather than committing music libraries or downloaded artwork.

## Build, Test, and Development Commands

Use Python 3.13 and the isolated `.venv`. Install development tools with `.\.venv\Scripts\python -m pip install -r requirements-dev.txt`.

After each Python code change, run `.\.venv\Scripts\python scripts/typecheck.py` and resolve diagnostics before moving to the next change. During TDD, a newly added test may deliberately fail until its implementation is added; finish each slice with the type checker and `.\.venv\Scripts\python -m unittest discover -s tests -v` passing. If a check cannot run, report the blocker explicitly rather than treating it as passed.

Use explicit annotations and control-flow checks to fix typing issues. Confine deliberate invalid-input casts to the relevant negative tests. Keep `pyrightconfig.json` shared with Pylance; preserve diagnostic coverage when fixing errors.

## Coding Style & Naming Conventions

Use four-space indentation for Python, `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep provider access separate from normalization rules and file writes. Preserve exact tag names and release-type values defined in `PLAN.md`. No formatter or linter is configured.

## Testing Guidelines

Tests use standard-library `unittest`; no coverage threshold is established. Add focused tests for folder parsing, original-release dates, conditional album artists, vinyl numbering, and release classification. Name test modules `test_<feature>.py`. Mock provider responses and use temporary copies for tag-writing tests. Verify artwork is at least 1000 × 1000 pixels and strictly below 5,000,000 bytes.

## Commit & Pull Request Guidelines

This checkout has no Git metadata, so no existing commit convention can be inferred. Use concise imperative subjects, such as `Add Discogs vinyl track mapping`. Describe behavior changes, reference relevant plan sections or issues, and report validation performed. Include screenshots for UI changes.

## Configuration & Data Safety

Keep API credentials outside tracked files. Preserve the planned review-before-apply workflow, and validate writes using disposable audio fixtures before touching a personal library.
