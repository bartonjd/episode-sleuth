# Contributing to EpisodeSleuth

Thanks for your interest in improving EpisodeSleuth. This document explains how
to set up a development environment, the layout of the codebase, and the checks
your changes should pass before you open a pull request.

EpisodeSleuth identifies unlabeled TV rips by their spoken dialogue: it
transcribes short audio samples, fingerprints the text, and matches it against a
library of reference episodes built from subtitles. It ships with a command-line
interface and a Fluent-style desktop GUI.

## Development setup

The project targets Python 3.9+ and uses a standard editable install.

```bash
# clone, then from the repository root:
python -m pip install -e .          # or: make install
python -m pip install pytest        # test runner (also: make dev)
```

`make dev` performs both steps in one go (editable install plus pytest).

FFmpeg must be available on your PATH for audio extraction, and a Vosk speech
model is needed to run identification end to end. See `INSTALL_LINUX.md` and
`INSTALL_WINDOWS.md` for platform-specific setup of those external
dependencies.

## The make workflow

A thin, self-documenting `Makefile` wraps every common task on Linux/macOS. Run
`make` (or `make help`) to list all targets. On Windows, `make.ps1` exposes the
same verbs, e.g. `pwsh make.ps1 build`.

| Target             | What it does                                             |
| ------------------ | ------------------------------------------------------- |
| `make install`     | Editable install of the package and runtime deps         |
| `make dev`         | `install` plus the pytest test dependency                |
| `make test`        | Run the test suite (`pytest -q`)                         |
| `make lint`        | Byte-compile every module to catch syntax errors         |
| `make clean`       | Remove build, packaging and Python cache artifacts       |
| `make clean-deep`  | `clean` plus remove the throwaway build venv             |
| `make build`       | Build a standalone one-folder binary in `dist/`          |
| `make package`     | Build the Linux release archives (tar.gz + .deb)         |
| `make dist`        | `clean`, then `build` and `package` from scratch         |

Two build switches are available: `BUNDLE_MODEL=1 make build` packs the Vosk
model for a fully offline app, and `ONEFILE=1 make build` produces a single-file
binary.

## Running the tests

```bash
make test        # or: python -m pytest -q
```

The suite lives in `tests/` and covers the engine modules (discovery, matcher,
scoring, duration lookup, config, and an integration path). Please keep it green
and add tests for new behavior. GUI code is validated by byte-compilation and
offscreen instantiation rather than a full rendered window.

## Type checking

The `engine` package is fully type-hinted and checked with mypy. Because the
repository root is itself an importable package, run mypy with explicit package
bases so `engine` is not discovered under two module names:

```bash
MYPYPATH=. python -m mypy engine --explicit-package-bases
```

The mypy configuration lives in the `[tool.mypy]` section of `pyproject.toml`
(it already targets `engine`). New or changed engine code should type-check
cleanly, including under `--disallow-untyped-defs`.

## Project structure

```
engine/               Core identification library (pure, importable, typed)
  types.py            Shared dataclasses and constants (e.g. DEFAULT_MEDIA_EXTS)
  discovery.py        Media discovery, filename parsing, Plex naming
  matcher.py          Per-file identification and orchestration
  scoring.py          Confidence scoring, metadata boosts, fuzzy fallback
  batch.py            Batch orchestration and result writers (CSV/JSON)
  duration_lookup.py  Episode runtime lookup (TVMaze + subtitle fallback)
cli/                  Command-line entry points (thin wrappers over engine)
  build_fingerprints.py   Build the reference library from subtitles
  identify.py             Identify a folder of rips
gui/                  Fluent-style desktop GUI (PySide6 + qfluentwidgets)
  main_window.py      Application shell and navigation
  workers.py          Background worker threads
  constants.py        Shared colors and UI constants
  pages/              One module per tab (identify, build, library, settings, log)
fingerprint_core.py   Low-level fingerprint database and raw scorers
subtitle_utils.py     Subtitle parsing and filename cleaning
stt_utils.py          Speech-to-text helpers (Vosk)
tests/                Test suite
```

The `engine` package is the stable core: the CLI and GUI are both thin layers on
top of it, so prefer putting reusable logic in `engine` and keeping the frontends
focused on argument parsing and presentation.

## Style conventions

- Use plain ASCII in source, comments and docs. Do not use em-dashes; use a
  hyphen instead.
- Follow the existing formatting of the module you are editing.
- Keep changes backwards compatible: the CLI, result exports and the GUI rename
  flow are all public surfaces.
- Add type hints to new engine code and keep the module docstrings accurate.

## License

EpisodeSleuth is licensed under the Apache License 2.0. By contributing you agree
that your contributions will be licensed under the same terms. See `LICENSE` and
`NOTICE` for details.
