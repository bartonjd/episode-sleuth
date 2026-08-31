#!/usr/bin/env python3
"""
config.py
=========
Unified, typed configuration for the phonetic DVD-episode identifier.

Historically the project kept two separate JSON files:

    * config.json      - engine/algorithm settings (STT, matching, fingerprint)
    * gui_config.json  - GUI-only preferences (theme, workers, last paths)

That worked but duplicated a few keys (max_workers, vosk model size, db_path)
and offered no schema/validation. This module introduces typed dataclasses that
sit ON TOP of the existing files:

    EngineConfig  - STT + database engine knobs (plus the full raw config.json)
    GuiConfig     - GUI preferences
    AppConfig     - root object combining engine + gui + shared db_path

Backward compatibility is the priority:

    * The two files remain the on-disk storage. Nothing about their format
      changes, so old config.json / gui_config.json files load unchanged.
    * AppConfig.load() reads whichever files exist and fills in defaults.
    * AppConfig.to_engine_dict() reproduces the exact config.json dict that the
      rest of the engine already consumes, so load_config() keeps returning the
      same shape it always did.

All new text uses a hyphen "-", never an em-dash.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
CONFIG_FILE = "config.json"        # engine settings
GUI_CONFIG_FILE = "gui_config.json"  # GUI preferences

# Valid enum values used by validation.
VALID_THEMES = ("Dark", "Light", "Auto")
VALID_MODEL_SIZES = ("small", "large")


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------
@dataclass
class EngineConfig:
    """Engine/backend configuration (STT + database).

    Commonly accessed knobs are promoted to typed fields; the complete
    config.json payload is preserved verbatim in ``raw`` so no engine section
    (fingerprint, matching, audio, opensubtitles, logging, ...) is ever lost.
    """
    stt_engine: str = "vosk"
    vosk_model_size: str = "small"          # "small" | "large"
    vosk_model_path: Optional[str] = None   # explicit override, else auto
    google_language: str = "en-US"
    db_path: str = "fingerprints.db"
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_engine_dict(cls, data: Dict[str, Any]) -> "EngineConfig":
        data = data or {}
        stt = data.get("stt", {}) if isinstance(data.get("stt"), dict) else {}
        db = data.get("database", {}) if isinstance(data.get("database"), dict) else {}
        return cls(
            stt_engine=stt.get("engine", "vosk"),
            vosk_model_size=stt.get("model_size", "small"),
            vosk_model_path=stt.get("vosk_model_path"),
            google_language=stt.get("google_language", "en-US"),
            db_path=db.get("path", "fingerprints.db"),
            raw=copy.deepcopy(data),
        )

    def to_engine_dict(self) -> Dict[str, Any]:
        """Rebuild the config.json-shaped dict the engine already consumes.

        Starts from the preserved raw payload (so every section survives) and
        overlays the typed knobs onto the ``stt`` / ``database`` sections.
        """
        out = copy.deepcopy(self.raw) if self.raw else {}
        stt = out.setdefault("stt", {})
        stt["engine"] = self.stt_engine
        stt["model_size"] = self.vosk_model_size
        stt["google_language"] = self.google_language
        if self.vosk_model_path:
            stt["vosk_model_path"] = self.vosk_model_path
        out.setdefault("database", {})["path"] = self.db_path
        return out


# ---------------------------------------------------------------------------
# GUI configuration
# ---------------------------------------------------------------------------
@dataclass
class GuiConfig:
    """GUI-specific preferences (persisted to gui_config.json)."""
    theme: str = "Dark"                 # "Dark" | "Light" | "Auto"
    theme_color: str = "#0078d4"
    max_workers: int = 4
    db_path: str = ""
    engine_config_path: str = ""
    last_source: str = ""
    last_subtitle_source: str = ""
    last_export_dir: str = ""
    last_rename_dest: str = ""
    last_show_title: str = ""
    samples_per_file: int = 5
    sample_length: float = 12.0
    review_confidence: float = 0.35
    vosk_model_size: str = "small"

    @classmethod
    def from_gui_dict(cls, data: Dict[str, Any]) -> "GuiConfig":
        data = data or {}
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)

    def to_gui_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------
@dataclass
class AppConfig:
    """Root configuration combining engine + GUI + shared settings."""
    db_path: str = "fingerprints.db"
    engine_config_path: str = ""
    engine: EngineConfig = field(default_factory=EngineConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)

    # ---- loading -------------------------------------------------------
    @classmethod
    def load(cls, config_dir: Optional[Path] = None,
             make_backups: bool = False) -> "AppConfig":
        """Load config.json + gui_config.json, merge, and apply defaults.

        Precedence for shared keys (db_path, vosk_model_size): a value set in
        the GUI file wins, because that is what the user last chose in the app.
        Missing files simply yield defaults, so a fresh install works and old
        installs keep working.
        """
        base = Path(config_dir) if config_dir else HERE
        engine_raw = _read_json(base / CONFIG_FILE)
        gui_raw = _read_json(base / GUI_CONFIG_FILE)

        engine = EngineConfig.from_engine_dict(engine_raw)
        gui = GuiConfig.from_gui_dict(gui_raw)

        # Optional one-time safety backups of the source files.
        if make_backups:
            for name in (CONFIG_FILE, GUI_CONFIG_FILE):
                _backup_once(base / name)

        # Shared-key merge: GUI overrides engine where the user made a choice.
        db_path = gui.db_path or engine.db_path or "fingerprints.db"
        if gui.vosk_model_size:
            engine.vosk_model_size = gui.vosk_model_size

        cfg = cls(
            db_path=db_path,
            engine_config_path=gui.engine_config_path or "",
            engine=engine,
            gui=gui,
        )
        for msg in cfg.validate():
            log.warning("config: %s", msg)
        return cfg

    # ---- saving --------------------------------------------------------
    def save(self, config_dir: Optional[Path] = None) -> None:
        """Write settings back to config.json and gui_config.json."""
        base = Path(config_dir) if config_dir else HERE
        # keep the two files in sync with the merged shared keys
        self.engine.db_path = self.db_path
        self.gui.db_path = self.db_path
        self.gui.vosk_model_size = self.engine.vosk_model_size
        _write_json(base / CONFIG_FILE, self.engine.to_engine_dict())
        _write_json(base / GUI_CONFIG_FILE, self.gui.to_gui_dict())

    # ---- interop -------------------------------------------------------
    def to_engine_dict(self) -> Dict[str, Any]:
        """Return the classic config.json dict engine callers already expect."""
        d = self.engine.to_engine_dict()
        d.setdefault("database", {})["path"] = self.db_path
        return d

    # ---- validation ----------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of human-readable problems; coerce invalid enums.

        Validation is non-fatal: bad enum values are reset to their defaults so
        the app always starts, and missing optional paths are only reported.
        """
        problems: List[str] = []

        if self.gui.theme not in VALID_THEMES:
            problems.append(
                f"theme '{self.gui.theme}' invalid; expected one of "
                f"{list(VALID_THEMES)} - falling back to 'Dark'.")
            self.gui.theme = "Dark"

        for label, val in (("gui.vosk_model_size", self.gui.vosk_model_size),
                           ("engine.vosk_model_size", self.engine.vosk_model_size)):
            if val not in VALID_MODEL_SIZES:
                problems.append(
                    f"{label} '{val}' invalid; expected one of "
                    f"{list(VALID_MODEL_SIZES)} - falling back to 'small'.")
        if self.gui.vosk_model_size not in VALID_MODEL_SIZES:
            self.gui.vosk_model_size = "small"
        if self.engine.vosk_model_size not in VALID_MODEL_SIZES:
            self.engine.vosk_model_size = "small"

        if not isinstance(self.gui.max_workers, int) or self.gui.max_workers < 1:
            problems.append(
                f"max_workers '{self.gui.max_workers}' invalid; must be >= 1 - "
                "falling back to 4.")
            self.gui.max_workers = 4

        # Optional paths: only warn if a non-empty path does not exist.
        if self.engine_config_path and not os.path.exists(self.engine_config_path):
            problems.append(
                f"engine_config_path '{self.engine_config_path}' does not exist.")
        if self.engine.vosk_model_path and not os.path.exists(self.engine.vosk_model_path):
            problems.append(
                f"vosk_model_path '{self.engine.vosk_model_path}' does not exist.")
        return problems


# ---------------------------------------------------------------------------
# Small JSON helpers (defensive: never raise on read)
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError as exc:
        log.warning("config: could not write %s (%s)", path, exc)


def _backup_once(path: Path) -> None:
    """Create a one-time .bak copy of ``path`` if it exists and none is present."""
    try:
        bak = path.with_suffix(path.suffix + ".bak")
        if path.exists() and not bak.exists():
            bak.write_bytes(path.read_bytes())
            log.info("config: wrote backup %s", bak)
    except OSError as exc:
        log.warning("config: could not back up %s (%s)", path, exc)


if __name__ == "__main__":
    # Quick self-check using a throwaway directory.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        # simulate an OLD-format pair of config files
        (dd / CONFIG_FILE).write_text(json.dumps({
            "stt": {"engine": "vosk",
                    "vosk_model_path": "models/vosk-model-small-en-us-0.15"},
            "database": {"path": "fingerprints.db"},
            "matching": {"confidence_threshold": 0.15},
        }))
        (dd / GUI_CONFIG_FILE).write_text(json.dumps({
            "theme": "Dark", "max_workers": 6, "vosk_model_size": "large",
            "db_path": "/tmp/my.db",
        }))
        cfg = AppConfig.load(dd, make_backups=True)
        assert cfg.db_path == "/tmp/my.db", cfg.db_path          # gui overrides
        assert cfg.engine.vosk_model_size == "large"            # merged
        assert cfg.gui.max_workers == 6
        # engine dict still carries the untouched matching section
        ed = cfg.to_engine_dict()
        assert ed["matching"]["confidence_threshold"] == 0.15
        assert ed["stt"]["model_size"] == "large"
        # invalid enum is coerced
        cfg.gui.theme = "Neon"
        assert any("theme" in p for p in cfg.validate())
        assert cfg.gui.theme == "Dark"
        cfg.save(dd)
        assert (dd / (CONFIG_FILE + ".bak")).exists()
        print("config self-check OK")
