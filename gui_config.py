#!/usr/bin/env python3
"""
gui_config.py
=============
Tiny persistence helper for the Fluent GUI (dvd_identifier_fluent.py).

This is deliberately SEPARATE from the engine's config.json. The engine config
holds algorithm/threshold settings that belong to the identification pipeline;
this file only remembers the user's GUI choices between sessions (which
fingerprint DB they picked, the last folder they browsed to, and their preferred
options). Keeping them apart means editing GUI preferences can never corrupt the
engine configuration, and vice versa.

The file lives next to this script as gui_config.json and is created on first
save. All reads are defensive: a missing or malformed file simply yields the
built-in defaults so the GUI always starts cleanly.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_CONFIG_PATH = os.path.join(HERE, "gui_config.json")

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
    "last_show_title": "",      # last TV show used for batch library import
    "theme": "Dark",            # "Dark" | "Light" | "Auto"
    "theme_color": "#0078d4",   # Windows 11 accent blue
}


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

    def __init__(self, path: str = GUI_CONFIG_PATH,
                 migrate_to_unified: bool | None = None):
        self.path = path
        if migrate_to_unified is not None:
            self._migrate_to_unified = migrate_to_unified
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ----- persistence -----
    def load(self) -> None:
        """Load settings from disk, merging over the defaults. Never raises."""
        if self._migrate_to_unified and self._load_unified():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key, val in stored.items():
                    # only keep known keys so stale keys cannot leak in
                    if key in DEFAULTS:
                        self._data[key] = val
        except (OSError, ValueError):
            # missing or corrupt -> keep defaults
            pass

    def save(self) -> None:
        """Write the current settings to disk. Never raises."""
        if self._migrate_to_unified and self._save_unified():
            return
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except OSError:
            pass

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
            from config import AppConfig, GuiConfig as _GuiSchema
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
