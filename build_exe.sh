#!/usr/bin/env bash
# =============================================================================
#  build_exe.sh - build a standalone EpisodeSleuth executable on Linux/macOS
#
#  Produces a self-contained one-folder bundle that runs the Fluent GUI without
#  requiring Python on the target machine:
#
#      dist/EpisodeSleuth/EpisodeSleuth
#
#  Usage:
#      ./build_exe.sh                 # normal one-folder build
#      BUNDLE_MODEL=1 ./build_exe.sh  # also pack the Vosk model (fully offline)
#      ONEFILE=1 ./build_exe.sh       # single-file executable (slower to start)
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

info "Creating build virtual environment (.buildvenv) ..."
if [ ! -d "$BUILD_VENV" ]; then
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

info "Running PyInstaller (this can take a few minutes) ..."
python -m PyInstaller "$PROJECT_DIR/episodesleuth.spec" --noconfirm

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
