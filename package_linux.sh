#!/usr/bin/env bash
# =============================================================================
#  package_linux.sh - build a distributable EpisodeSleuth package for Linux x64
#
#  Produces, in dist/:
#      EpisodeSleuth-<version>-linux-x64.tar.gz   (universal, always)
#      episodesleuth_<version>_amd64.deb          (if dpkg-deb is available
#                                                   or you pass --deb)
#
#  The .tar.gz contains the standalone one-folder binary bundle plus a small
#  install.sh / uninstall.sh, a .desktop launcher and an icon. The end user
#  just extracts it and runs ./install.sh - no Python required.
#
#  Usage:
#      ./package_linux.sh              # build binary if needed, then package
#      ./package_linux.sh --build      # always rebuild the binary first
#      ./package_linux.sh --deb        # also build a .deb (needs dpkg-deb)
#      ./package_linux.sh --no-deb     # never build a .deb
#      BUNDLE_MODEL=1 ./package_linux.sh   # package a model-bundled build
#
#  Only the one-folder build is packaged (ONEFILE builds are for quick sharing,
#  not for installers).
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

info() { printf '\033[36m[*] %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m[OK] %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$1"; }

# --- parse args -------------------------------------------------------------
FORCE_BUILD=0
WANT_DEB="auto"
for arg in "$@"; do
    case "$arg" in
        --build)  FORCE_BUILD=1 ;;
        --deb)    WANT_DEB="yes" ;;
        --no-deb) WANT_DEB="no" ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
            exit 0 ;;
        *) warn "Ignoring unknown argument: $arg" ;;
    esac
done

# --- version ----------------------------------------------------------------
VERSION="$(grep -oP '__version__\s*=\s*"\K[^"]+' __init__.py 2>/dev/null || echo "0.0.0")"
ARCH="x64"
DEB_ARCH="amd64"
PKG_NAME="EpisodeSleuth-${VERSION}-linux-${ARCH}"
BUNDLE_DIR="dist/EpisodeSleuth"

info "Packaging EpisodeSleuth ${VERSION} for Linux ${ARCH}"

# --- build the binary if needed ---------------------------------------------
if [ "$FORCE_BUILD" = "1" ] || [ ! -x "$BUNDLE_DIR/EpisodeSleuth" ]; then
    info "Building standalone binary (build_binary.sh) ..."
    ./build_binary.sh
else
    ok "Reusing existing binary at $BUNDLE_DIR"
fi

if [ ! -x "$BUNDLE_DIR/EpisodeSleuth" ]; then
    echo "Build did not produce $BUNDLE_DIR/EpisodeSleuth" >&2
    exit 1
fi

# --- pick an icon (PNG) -----------------------------------------------------
ICON_SRC=""
for cand in packaging/assets/Square150x150Logo.png packaging/assets/StoreLogo.png packaging/assets/Square44x44Logo.png; do
    if [ -f "$cand" ]; then ICON_SRC="$cand"; break; fi
done

# --- stage the package tree -------------------------------------------------
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG_ROOT="$STAGE/$PKG_NAME"
mkdir -p "$PKG_ROOT"

info "Staging bundle ..."
cp -a "$BUNDLE_DIR" "$PKG_ROOT/EpisodeSleuth"
if [ -n "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$PKG_ROOT/episodesleuth.png"
fi

# --- .desktop template (Exec/Icon get rewritten by install.sh) --------------
cat > "$PKG_ROOT/episodesleuth.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=EpisodeSleuth
GenericName=Episode Identifier
Comment=Phonetic dialogue fingerprinting for TV & movie identification
Exec=__EXEC__
Icon=__ICON__
Terminal=false
Categories=AudioVideo;
Keywords=audio;fingerprint;subtitle;episode;identify;speech;
DESKTOP

# --- install.sh (runs on the target machine) --------------------------------
cat > "$PKG_ROOT/install.sh" <<'INSTALL'
#!/usr/bin/env bash
# Install EpisodeSleuth (standalone binary) on this machine.
#   sudo ./install.sh      -> system-wide  (/opt/episodesleuth)
#        ./install.sh      -> current user (~/.local/share/episodesleuth)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="EpisodeSleuth"

if [ "$(id -u)" -eq 0 ]; then
    PREFIX="/opt/episodesleuth"
    BIN_DIR="/usr/local/bin"
    DESKTOP_DIR="/usr/share/applications"
    ICON_DIR="/usr/share/icons/hicolor/256x256/apps"
    SCOPE="system-wide"
else
    PREFIX="$HOME/.local/share/episodesleuth"
    BIN_DIR="$HOME/.local/bin"
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
    SCOPE="current user"
fi

echo "[*] Installing EpisodeSleuth ($SCOPE) to $PREFIX ..."
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
cp -a "$HERE/$APP/." "$PREFIX/"

EXEC_PATH="$PREFIX/EpisodeSleuth"
chmod +x "$EXEC_PATH"

# Symlink onto PATH
ln -sf "$EXEC_PATH" "$BIN_DIR/episodesleuth"

# Icon
ICON_NAME="episodesleuth"
if [ -f "$HERE/episodesleuth.png" ]; then
    cp "$HERE/episodesleuth.png" "$ICON_DIR/episodesleuth.png"
else
    ICON_NAME="$EXEC_PATH"  # fall back to no themed icon
fi

# Desktop entry (rewrite Exec/Icon placeholders)
sed -e "s|__EXEC__|$EXEC_PATH|g" -e "s|__ICON__|$ICON_NAME|g" \
    "$HERE/episodesleuth.desktop" > "$DESKTOP_DIR/episodesleuth.desktop"
chmod 644 "$DESKTOP_DIR/episodesleuth.desktop"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -q "$(dirname "$(dirname "$(dirname "$ICON_DIR")")")" >/dev/null 2>&1 || true

echo "[OK] Installed."
echo "     Launch from your app menu (EpisodeSleuth) or run: episodesleuth"
case ":$PATH:" in
    *":$BIN_DIR:"*) : ;;
    *) echo "     NOTE: $BIN_DIR is not on your PATH; add it or run $EXEC_PATH directly." ;;
esac
echo "     Reminder: ffmpeg must be installed (e.g. sudo apt install ffmpeg)."
INSTALL
chmod +x "$PKG_ROOT/install.sh"

# --- uninstall.sh -----------------------------------------------------------
cat > "$PKG_ROOT/uninstall.sh" <<'UNINSTALL'
#!/usr/bin/env bash
# Remove an EpisodeSleuth install created by install.sh.
set -euo pipefail
if [ "$(id -u)" -eq 0 ]; then
    PREFIX="/opt/episodesleuth"; BIN_DIR="/usr/local/bin"
    DESKTOP_DIR="/usr/share/applications"; ICON_DIR="/usr/share/icons/hicolor/256x256/apps"
else
    PREFIX="$HOME/.local/share/episodesleuth"; BIN_DIR="$HOME/.local/bin"
    DESKTOP_DIR="$HOME/.local/share/applications"; ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
fi
echo "[*] Removing EpisodeSleuth ..."
rm -rf "$PREFIX"
rm -f "$BIN_DIR/episodesleuth" "$DESKTOP_DIR/episodesleuth.desktop" "$ICON_DIR/episodesleuth.png"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
echo "[OK] Removed."
UNINSTALL
chmod +x "$PKG_ROOT/uninstall.sh"

# --- README inside the package ----------------------------------------------
cat > "$PKG_ROOT/README-INSTALL.txt" <<README
EpisodeSleuth ${VERSION} - Linux x64 standalone build
=====================================================

This bundle runs WITHOUT Python installed. It contains a self-contained binary.

Quick start (no install)
-------------------------
  ./EpisodeSleuth/EpisodeSleuth

Install (recommended)
---------------------
  ./install.sh          # installs for the current user (~/.local)
  sudo ./install.sh     # installs system-wide (/opt, on PATH for everyone)

After installing you can launch "EpisodeSleuth" from your desktop app menu, or
run 'episodesleuth' from a terminal.

Uninstall
---------
  ./uninstall.sh        # match the scope you installed with (use sudo if needed)

Requirements
------------
  * ffmpeg must be installed for audio decoding:
        sudo apt install ffmpeg        (Debian/Ubuntu)
        sudo dnf install ffmpeg        (Fedora)
  * The offline speech model is downloaded on first run from the app's Settings
    page, unless this build already bundles it.

The EpisodeSleuth/ folder is self-contained: the EpisodeSleuth executable and
its _internal/ directory must stay together.
README

# --- tar.gz -----------------------------------------------------------------
mkdir -p dist
TARBALL="dist/${PKG_NAME}.tar.gz"
info "Creating $TARBALL ..."
tar -C "$STAGE" -czf "$TARBALL" "$PKG_NAME"
ok "Wrote $TARBALL ($(du -h "$TARBALL" | cut -f1))"

# --- optional .deb ----------------------------------------------------------
build_deb() {
    local debroot="$STAGE/deb"
    local prefix="$debroot/opt/episodesleuth"
    mkdir -p "$prefix" "$debroot/usr/local/bin" \
             "$debroot/usr/share/applications" \
             "$debroot/usr/share/icons/hicolor/256x256/apps" \
             "$debroot/DEBIAN"
    cp -a "$BUNDLE_DIR/." "$prefix/"
    chmod +x "$prefix/EpisodeSleuth"
    ln -sf /opt/episodesleuth/EpisodeSleuth "$debroot/usr/local/bin/episodesleuth"
    local icon_name="episodesleuth"
    if [ -n "$ICON_SRC" ]; then
        cp "$ICON_SRC" "$debroot/usr/share/icons/hicolor/256x256/apps/episodesleuth.png"
    else
        icon_name="/opt/episodesleuth/EpisodeSleuth"
    fi
    sed -e "s|__EXEC__|/opt/episodesleuth/EpisodeSleuth|g" -e "s|__ICON__|$icon_name|g" \
        "$PKG_ROOT/episodesleuth.desktop" > "$debroot/usr/share/applications/episodesleuth.desktop"

    local instsize
    instsize="$(du -ks "$prefix" | cut -f1)"
    cat > "$debroot/DEBIAN/control" <<CTRL
Package: episodesleuth
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: ${DEB_ARCH}
Depends: ffmpeg
Installed-Size: ${instsize}
Maintainer: EpisodeSleuth contributors
Description: Phonetic dialogue fingerprinting for TV & movie identification
 EpisodeSleuth identifies unlabeled TV and movie files by matching the
 speech in their audio against phonetic fingerprints built from subtitles.
 This package ships a self-contained build; no Python is required.
CTRL
    cat > "$debroot/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database >/dev/null 2>&1 || true
exit 0
POST
    chmod 755 "$debroot/DEBIAN/postinst"

    local debfile="dist/episodesleuth_${VERSION}_${DEB_ARCH}.deb"
    dpkg-deb --build --root-owner-group "$debroot" "$debfile" >/dev/null
    ok "Wrote $debfile ($(du -h "$debfile" | cut -f1))"
}

if [ "$WANT_DEB" = "no" ]; then
    info "Skipping .deb (--no-deb)."
elif command -v dpkg-deb >/dev/null 2>&1; then
    info "Building .deb package ..."
    build_deb
elif [ "$WANT_DEB" = "yes" ]; then
    warn "dpkg-deb not found; cannot build .deb. Install dpkg and retry."
else
    info "dpkg-deb not found; skipping .deb (tar.gz still produced)."
fi

ok "Done. Artifacts are in dist/."
