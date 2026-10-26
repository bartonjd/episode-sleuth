#!/usr/bin/env python3
"""
gui_config.py
=============
Tiny persistence helper for the Fluent GUI (audio_fingerprint.gui package).

This is deliberately SEPARATE from the engine's config.json. The engine config
holds algorithm/threshold settings that belong to the identification pipeline;
this file only remembers the user's GUI choices between sessions (which
fingerprint DB they picked, the last folder they browsed to, and their preferred
options). Keeping them apart means editing GUI preferences can never corrupt the
engine configuration, and vice versa.

The file lives in a per-user, writable config directory (see
``gui_config_dir``) and is created on first run. All reads are defensive: a
missing or malformed file simply yields the built-in defaults so the GUI always
starts cleanly.

Why a per-user directory (and why no registry entry):
    Earlier versions stored gui_config.json next to this script. When the app is
    installed via MSIX that directory lives inside the read-only WindowsApps
    folder, so every save silently failed and nothing persisted between runs.
    The fix is to compute a well-known per-user path at startup:

        Windows:      %APPDATA%\\EpisodeSleuth\\gui_config.json
        Linux/macOS:  $XDG_CONFIG_HOME/episodesleuth/gui_config.json
                      (defaulting to ~/.config/episodesleuth/)

    No Windows registry entry is needed: the location is a deterministic,
    per-user path derived from standard environment variables every run, so
    there is nothing to look up or remember. %APPDATA% is the real roaming
    AppData folder; even if MSIX virtualises writes into the package's
    LocalCache, the value still round-trips between runs, which is what matters.
    The directory can be overridden with the EPISODESLEUTH_CONFIG_DIR
    environment variable (used by tests and portable installs).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from typing import Any, Dict

log = logging.getLogger(__name__)


def _native_path(path: str) -> str:
    """Translate separators to the running OS via the shared path layer.

    Imported lazily: gui/__init__.py eagerly imports main_window, which imports
    this module, so a top-level ``from gui.path_utils import native_path`` would
    create a circular import. By the time these helpers are *called* the gui
    package is fully initialised, so the lazy import is safe.
    """
    from gui.path_utils import native_path
    return native_path(path)

HERE = os.path.dirname(os.path.abspath(__file__))
# Legacy location (next to the program). Only read from, for one-time migration.
LEGACY_GUI_CONFIG_PATH = os.path.join(HERE, "gui_config.json")
# Environment override for the config directory (tests / portable use).
CONFIG_DIR_ENV = "EPISODESLEUTH_CONFIG_DIR"


def gui_config_dir() -> str:
    """Return the per-user, writable directory for the GUI config file.

    Honours the EPISODESLEUTH_CONFIG_DIR override first, then falls back to the
    platform-standard per-user location. The returned path uses OS-native
    separators via :func:`native_path`.
    """
    override = os.getenv(CONFIG_DIR_ENV)
    if override:
        return _native_path(override)
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        return _native_path(os.path.join(base, "EpisodeSleuth"))
    xdg = os.getenv("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return _native_path(os.path.join(xdg, "episodesleuth"))


def gui_config_path() -> str:
    """Return the full path to the per-user gui_config.json."""
    return _native_path(os.path.join(gui_config_dir(), "gui_config.json"))


def default_db_path() -> str:
    """Return a per-user, platform-appropriate default database path.

    Shared by the Build and Identify pages so both agree on where the
    fingerprint library lives when the user has not chosen a location:

        Windows:      %APPDATA%\\EpisodeSleuth\\fingerprints.db
        Linux/macOS:  ~/.local/share/episodesleuth/fingerprints.db
    """
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "EpisodeSleuth", "fingerprints.db")
    return os.path.join(
        os.path.expanduser("~"), ".local", "share", "episodesleuth",
        "fingerprints.db")

# Built-in defaults. Any key missing from the on-disk file falls back to these.
DEFAULTS: Dict[str, Any] = {
    "db_path": "",
    "engine_config_path": "",
    "last_source": "",
    "last_subtitle_source": "",
    "last_export_dir": "",
    "last_rename_dest": "",
    "samples_per_file": 5,
    "sample_length": 12.0,
    "max_workers": 4,           # files identified in parallel
    "review_confidence": 0.35,
    "vosk_model_size": "small",  # "small" (~40MB) | "large" (~1.8GB, accurate)
    # Primary dialogue language of the library. "Auto-detect" lets the build
    # detect each episode's language; a specific choice applies it everywhere.
    # Drives the phonetic strategy (metaphone for English, raw words otherwise)
    # and Vosk model selection. One of: Auto-detect | English | Spanish |
    # French | German | Other.
    "primary_language": "Auto-detect",
    "last_show_title": "",      # last TV show used for batch library import
    # Extensions scanned when identifying a folder (comma-separated). "" lets the
    # engine fall back to its built-in media-extension set.
    "media_extensions": "",
    # How deep to scan subfolders when identifying a folder:
    #   0  = this folder only (no recursion)
    #   1  = one level down (the folder and its immediate subfolders)
    #  -1  = all subfolders (unlimited recursion)
    "scan_depth": 1,
    # Treat "Part 1" / "(1)" multi-part title punctuation as equivalent.
    "ignore_part_format_differences": True,
    "theme": "Dark",            # "Dark" | "Light" | "Auto"
    "theme_color": "#0078d4",   # Windows 11 accent blue
    # Global view preferences (apply to the Identify page layout).
    "view_density": "Standard",  # Compact | Standard | Comfortable
    "view_mode": "Advanced",     # Simple | Advanced
    # Main-window geometry (size + position), stored as a base64 string
    # produced by QMainWindow.saveGeometry(); "" means use the default size.
    "window_geometry": "",
    # Identify-page vertical splitter sizes [top_px, table_px]; [] = default.
    "identify_splitter": [],
    # Identify-page UI state (status filter, table layout, dry-run preference).
    # Nested so all Identify-page persistence lives under one key.
    "identify_page": {
        "status_filter": "All",     # All | Rename | Correct | Review
        "dry_run_preview": False,   # always show a preview before renaming
        "column_widths": {},        # {column_index_str: width_px}
        "sort_col": -1,             # -1 = unsorted (input order)
        "sort_order": 0,            # 0 = ascending, 1 = descending
        # Which optional panels are shown in Advanced mode (the Customize View
        # dialog toggles these). Ignored in Simple mode, which force-hides the
        # advanced panels for a clean, uncluttered layout.
        "custom_panels": {
            "options": True,        # sample/worker options card
            "legend": True,         # status colour legend
            "dry_run": True,        # "always preview" preference row
            "exports": True,        # Export JSON / CSV buttons
        },
    },
}


def _clamp_int(value: Any, lo: int, hi: int, fallback: int) -> int:
    """Coerce ``value`` to an int within [lo, hi]; fall back if uncoercible."""
    try:
        out = int(float(value))  # float() first so "5.0" / 5.0 also work
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, out))


def _clamp_float(value: Any, lo: float, hi: float, fallback: float) -> float:
    """Coerce ``value`` to a float within [lo, hi]; fall back if uncoercible."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, out))


class GuiConfig:
    """Dict-like wrapper around gui_config.json with typed getters.

    The public interface (``get`` / ``set`` / ``update`` / ``save`` / ``load`` /
    ``as_dict``) is unchanged, so existing callers keep working. When
    ``_migrate_to_unified`` is True the class delegates its on-disk storage to
    the unified :class:`config.AppConfig` (which still reads and writes
    gui_config.json), giving a single validated source of truth without changing
    the file format.
    """

    # Opt-in delegation to the unified config system (config.AppConfig).
    _migrate_to_unified: bool = False

    # Numeric keys that must be coerced + clamped on load so a malformed value
    # (a string, null, float-where-int, or out-of-range number from an older or
    # hand-edited file) can never leave a SpinBox blank or out of range.
    _INT_RANGES: Dict[str, tuple] = {
        "samples_per_file": (1, 15),
        "max_workers": (1, 16),
    }
    _FLOAT_RANGES: Dict[str, tuple] = {
        "sample_length": (4.0, 30.0),
        "review_confidence": (0.05, 0.95),
    }
    _SCAN_DEPTH_VALID = (0, 1, -1)

    def __init__(self, path: str | None = None,
                 migrate_to_unified: bool | None = None):
        # Resolve the per-user path at construction time (not import time) so a
        # test or launcher that sets EPISODESLEUTH_CONFIG_DIR is always honoured.
        self.path = _native_path(path) if path else gui_config_path()
        if migrate_to_unified is not None:
            self._migrate_to_unified = migrate_to_unified
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ----- persistence -----
    def load(self) -> None:
        """Load settings from disk, merging over the defaults. Never raises.

        On first run (no file at the per-user path) a legacy gui_config.json
        sitting next to the program is migrated over, then the file is written
        so the user can find and edit it. All values are coerced to the correct
        type and clamped to range.
        """
        if self._migrate_to_unified and self._load_unified():
            self._coerce_types()
            return

        if not os.path.exists(self.path):
            self._migrate_legacy()
        self._read_file(self.path)
        self._coerce_types()

        # First-run: materialise the file at the per-user location so it exists
        # and is discoverable. Failures are logged by save(), never fatal.
        if not os.path.exists(self.path):
            self.save()

    def _read_file(self, path: str) -> bool:
        """Merge known keys from the JSON file at ``path``. Returns success."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            # missing or corrupt -> keep whatever we have (defaults)
            return False
        if isinstance(stored, dict):
            for key, val in stored.items():
                # only keep known keys so stale keys cannot leak in
                if key in DEFAULTS:
                    self._data[key] = val
            return True
        return False

    def _migrate_legacy(self) -> None:
        """One-time copy of a legacy gui_config.json next to the program."""
        legacy = _native_path(LEGACY_GUI_CONFIG_PATH)
        if legacy == self.path or not os.path.exists(legacy):
            return
        if self._read_file(legacy):
            log.info("Migrated legacy GUI config from %s to %s",
                     legacy, self.path)

    def _coerce_types(self) -> None:
        """Coerce + clamp numeric settings so widgets never receive bad values."""
        for key, (lo, hi) in self._INT_RANGES.items():
            self._data[key] = _clamp_int(self._data.get(key), lo, hi,
                                         DEFAULTS[key])
        for key, (lo, hi) in self._FLOAT_RANGES.items():
            self._data[key] = _clamp_float(self._data.get(key), lo, hi,
                                           DEFAULTS[key])
        depth = self._data.get("scan_depth", DEFAULTS["scan_depth"])
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            depth = DEFAULTS["scan_depth"]
        self._data["scan_depth"] = depth if depth in self._SCAN_DEPTH_VALID \
            else DEFAULTS["scan_depth"]

    def save(self) -> None:
        """Write the current settings to disk atomically. Never raises.

        The directory is created if needed, the file is written to a temporary
        sibling and then atomically renamed into place (os.replace) so a crash
        mid-write cannot corrupt the config. Unlike before, a failure is logged
        (visible on the Log page) instead of being silently swallowed.
        """
        if self._migrate_to_unified and self._save_unified():
            return
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            # Atomic write: temp file in the same directory, then os.replace.
            fd, tmp = tempfile.mkstemp(
                prefix=".gui_config_", suffix=".tmp", dir=directory or None)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except OSError as exc:
            log.warning("Could not save GUI config to %s: %s", self.path, exc)

    # ----- unified-config delegation (opt-in) -----
    def _load_unified(self) -> bool:
        """Populate ``self._data`` from config.AppConfig. Returns success."""
        try:
            from config import AppConfig
            app = AppConfig.load()
            gui = app.gui.to_gui_dict()
            for key in DEFAULTS:
                if key in gui:
                    self._data[key] = gui[key]
            # shared keys live on the root of AppConfig
            self._data["db_path"] = app.db_path
            self._data["engine_config_path"] = app.engine_config_path
            return True
        except Exception:
            return False

    def _save_unified(self) -> bool:
        """Write ``self._data`` back through config.AppConfig. Returns success."""
        try:
            from config import AppConfig
            from config import GuiConfig as _GuiSchema
            app = AppConfig.load()
            known = set(_GuiSchema.__dataclass_fields__)  # type: ignore[attr-defined]
            app.gui = _GuiSchema.from_gui_dict(
                {k: v for k, v in self._data.items() if k in known})
            app.db_path = self._data.get("db_path", app.db_path)
            app.engine_config_path = self._data.get(
                "engine_config_path", app.engine_config_path)
            app.save()
            return True
        except Exception:
            return False

    # ----- access -----
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            self._data[key] = value

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)


if __name__ == "__main__":
    # quick self-check
    cfg = GuiConfig(os.path.join(HERE, "_gui_config_test.json"))
    cfg.set("db_path", "/tmp/fingerprints.db")
    cfg.set("samples_per_file", 7)
    cfg.save()
    again = GuiConfig(cfg.path)
    assert again.get("db_path") == "/tmp/fingerprints.db"
    assert again.get("samples_per_file") == 7
    os.remove(cfg.path)
    print("gui_config self-check OK")
