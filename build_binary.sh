#!/usr/bin/env bash
# =============================================================================
#  build_binary.sh - build a standalone EpisodeSleuth binary on Linux/macOS
#
#  Produces a self-contained one-folder bundle that runs the Fluent GUI without
#  requiring Python on the target machine:
#
#      dist/EpisodeSleuth/EpisodeSleuth
#
#  Usage:
#      ./build_binary.sh                 # normal one-folder build
#      BUNDLE_MODEL=1 ./build_binary.sh  # also pack the Vosk model (fully offline)
#      ONEFILE=1 ./build_binary.sh       # single-file binary (slower to start)
#
#  It builds inside a throwaway .buildvenv so your system Python stays clean.
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

BUILD_VENV="$PROJECT_DIR/.buildvenv"
PY="python3"

info() { printf '\033[36m[*] %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m[OK] %s\033[0m\n' "$1"; }

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "python3 not found on PATH." >&2
    exit 1
fi

# Validate the venv, not just the folder: a .buildvenv copied from another OS
# (e.g. a Windows venv shipped inside a zip) has a Scripts/ layout and a
# pyvenv.cfg whose "home" points at a non-existent interpreter path, which
# breaks PyInstaller. Recreate whenever the Linux launcher is missing/unusable.
if [ -d "$BUILD_VENV" ] && ! "$BUILD_VENV/bin/python" --version >/dev/null 2>&1; then
    info "Existing .buildvenv is not a valid Linux venv - removing and recreating ..."
    rm -rf "$BUILD_VENV"
fi
if [ ! -d "$BUILD_VENV" ]; then
    info "Creating build virtual environment (.buildvenv) ..."
    "$PY" -m venv "$BUILD_VENV"
fi
# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"

info "Installing PyInstaller + runtime dependencies ..."
python -m pip install --upgrade pip >/dev/null
python -m pip install pyinstaller >/dev/null
python -m pip install -r "$PROJECT_DIR/requirements.txt" >/dev/null
ok "Build dependencies installed."

info "Cleaning previous build output ..."
rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist"
# Purge stale __pycache__ so a rebuild can never pick up old bytecode (e.g. an
# outdated APP_TITLE). Guarantees the binary reflects the current source.
find "$PROJECT_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

info "Running PyInstaller (this can take a few minutes) ..."
# --clean discards PyInstaller's own cached analysis/bytecode as an extra guard.
python -m PyInstaller "$PROJECT_DIR/episodesleuth.spec" --noconfirm --clean

deactivate

if [ "${ONEFILE:-0}" = "1" ]; then
    TARGET="$PROJECT_DIR/dist/EpisodeSleuth"
else
    TARGET="$PROJECT_DIR/dist/EpisodeSleuth/EpisodeSleuth"
fi

if [ -e "$TARGET" ]; then
    ok "Build complete."
    echo ""
    echo "  Run it with:"
    echo "      $TARGET"
    echo ""
    echo "  (Distribute the whole dist/EpisodeSleuth folder for one-folder builds.)"
else
    echo "Build finished but the expected executable was not found at:" >&2
    echo "  $TARGET" >&2
    exit 1
fi
