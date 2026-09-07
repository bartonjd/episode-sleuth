#!/usr/bin/env bash
# =============================================================================
#  clean.sh - remove build, packaging and Python cache artifacts (Linux/macOS)
#
#  Deletes everything that the build/package scripts generate, so you get back
#  to a pristine source tree. Source files, docs, the fingerprint database and
#  downloaded Vosk models are left untouched.
#
#  Usage:
#      ./clean.sh              # remove build artifacts and caches
#      ./clean.sh --deep       # also remove the throwaway build venv (.buildvenv)
#      ./clean.sh --dry-run    # show what would be removed, delete nothing
#
#  On Windows use clean.ps1 (or "pwsh make.ps1 clean").
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

DEEP=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --deep)    DEEP=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

info() { printf '\033[36m[*] %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m[OK] %s\033[0m\n' "$1"; }

# Remove a path (file, dir or glob) with dry-run support.
rm_path() {
    local target="$1"
    if [ -e "$target" ] || [ -L "$target" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "  would remove: $target"
        else
            rm -rf "$target"
            echo "  removed: $target"
        fi
    fi
}

info "Cleaning build and packaging artifacts ..."
# PyInstaller / packaging output.
rm_path "build"
rm_path "dist"
# Windows installer / signing artifacts (harmless no-ops on Linux).
for f in *.msix *.pfx *.cer; do rm_path "$f"; done
# Generated one-click launchers.
rm_path "Launch_DVD_Identifier.bat"
rm_path "Launch_EpisodeSleuth.bat"

info "Cleaning Python packaging metadata ..."
for d in *.egg-info; do rm_path "$d"; done
rm_path ".eggs"

info "Cleaning auto-generated PyInstaller specs (keeping episodesleuth.spec) ..."
if [ "$DRY_RUN" -eq 1 ]; then
    find . -maxdepth 1 -name '*.spec' ! -name 'episodesleuth.spec' \
        -print 2>/dev/null | sed 's/^/  would remove: /' || true
else
    find . -maxdepth 1 -name '*.spec' ! -name 'episodesleuth.spec' \
        -delete 2>/dev/null || true
fi

info "Cleaning Python caches ..."
if [ "$DRY_RUN" -eq 1 ]; then
    find . -type d -name '__pycache__' -not -path './.buildvenv/*' \
        -print 2>/dev/null | sed 's/^/  would remove: /' || true
    find . -type f \( -name '*.pyc' -o -name '*.pyo' \) \
        -not -path './.buildvenv/*' -print 2>/dev/null \
        | sed 's/^/  would remove: /' || true
else
    find . -type d -name '__pycache__' -not -path './.buildvenv/*' \
        -exec rm -rf {} + 2>/dev/null || true
    find . -type f \( -name '*.pyc' -o -name '*.pyo' \) \
        -not -path './.buildvenv/*' -delete 2>/dev/null || true
fi
rm_path ".pytest_cache"
rm_path ".coverage"

if [ "$DEEP" -eq 1 ]; then
    info "Deep clean: removing throwaway build venv ..."
    rm_path ".buildvenv"
fi

ok "Clean complete."
