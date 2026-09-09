# Changelog

All notable changes to EpisodeSleuth are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Identify page status filter: an All / Rename / Correct / Review segmented
  control above the results table, with live per-status counts on each tab.
  The filter works together with the text search and its selection is
  remembered between runs.
- Dry-run "Preview Renames" button that shows a before/after dialog of every
  planned rename, plus an "Always preview" checkbox so the confirmation can be
  made the default.
- Undo for the last rename batch: rename operations are logged to
  `~/.episode_sleuth/rename_history.json`, an "Undo Last Rename" button reverts
  the most recent batch (validating that each file still exists), and now-empty
  season/show folders are pruned automatically.
- Table UI state persistence: column widths, sort column and sort order are
  saved to the GUI config and restored on the next launch.
- Continuous integration: a GitHub Actions workflow that lints (ruff) and runs
  the test suite on every push and pull request across Python 3.9-3.12, and
  builds standalone Linux and Windows artifacts on version tags by running the
  project's own `make dist` / `make.ps1 dist` front-doors.
- Tooling for maintainers: a `make lint` (and `make lint-fix`) target backed by
  ruff, a pinned `requirements-build.txt`, a `.pre-commit-config.yaml`, and a
  `make bump-version VERSION=x.y.z` target (via `tools/bump_version.py`) that
  updates the version in `__init__.py`, `setup.py` and this changelog.

### Changed
- `make lint` now runs ruff (configured in `pyproject.toml`); the previous
  byte-compile check is still available as `make compile`.
- Cleaned up unused imports and import ordering across the codebase so the
  tree lints cleanly under ruff.

## [1.0.0]

### Added
- Phonetic "Shazam for dialogue" engine: identifies TV/movie episodes from
  DVD-rip audio by transcribing short speech samples and matching them
  (phonetically) against a subtitle-built reference database.
- Fluent Design desktop GUI (PySide6 + PySide6-Fluent-Widgets) with Identify,
  Build, Library, Settings and Log pages.
- Command-line entry points: `episodesleuth`, `episodesleuth-identify` and
  `episodesleuth-fingerprint` (plus `dvd-*` backwards-compatible aliases).
- Confidence pills, a colour legend and status badges on the Identify results,
  a results count, and a part-format-tolerant title comparison.
- Media-extension filter so only real video/audio files are scanned.
- Library management UI with an overwrite option and episode duration
  validation.
- Cancellable identification runs and a configurable ambiguity gate.
- Cross-platform packaging: a standalone one-folder binary via PyInstaller, a
  Linux `.tar.gz` and `.deb`, and a Windows `.zip` (and optional MSIX), driven
  by `make` on Linux/macOS and `make.ps1` on Windows.
- Type hints on the identification engine and a `CONTRIBUTING.md` guide.

[Unreleased]: https://github.com/bartonjd/episode-sleuth/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bartonjd/episode-sleuth/releases/tag/v1.0.0
