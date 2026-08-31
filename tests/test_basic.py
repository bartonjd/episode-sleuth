"""Basic smoke tests for the audio-fingerprint package.

These are intentionally lightweight so the suite runs without a Vosk model,
ffmpeg, or a populated database. They verify that packaging metadata and the
core (dependency-light) modules import correctly.
"""
import os
import sys

# Make sure the project root is importable when running pytest from anywhere.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_trivial():
    """A trivial always-passing test so pytest has something to collect."""
    assert True


def test_version_string():
    """The package exposes a version string."""
    import __init__ as pkg  # flat-layout package module (project root)
    assert isinstance(pkg.__version__, str)
    assert pkg.__version__.count(".") >= 1


def test_core_imports():
    """Dependency-light core modules import without side effects."""
    import fingerprint_core
    import subtitle_utils

    # A couple of key symbols exist.
    assert hasattr(fingerprint_core, "score_matches")
    assert hasattr(subtitle_utils, "parse_episode_info")


def test_parse_episode_info():
    """The subtitle episode parser handles a standard SxxExx filename."""
    from subtitle_utils import parse_episode_info

    season, episode, _title = parse_episode_info("Matlock (1986) - S01E03 - The Judge")
    assert season == 1
    assert episode == 3


def test_config_module_imports():
    """The unified config dataclasses import and expose expected fields."""
    from config import AppConfig, EngineConfig, GuiConfig

    app = AppConfig()
    assert isinstance(app.engine, EngineConfig)
    assert isinstance(app.gui, GuiConfig)
    assert app.gui.theme == "Dark"
    assert app.engine.vosk_model_size == "small"


def test_config_loads_old_files(tmp_path):
    """Old-format config.json + gui_config.json load and merge correctly."""
    import json
    from config import AppConfig, CONFIG_FILE, GUI_CONFIG_FILE

    (tmp_path / CONFIG_FILE).write_text(json.dumps({
        "stt": {"engine": "vosk",
                "vosk_model_path": "models/vosk-model-small-en-us-0.15"},
        "database": {"path": "fingerprints.db"},
        "matching": {"confidence_threshold": 0.15},
    }))
    (tmp_path / GUI_CONFIG_FILE).write_text(json.dumps({
        "theme": "Light", "max_workers": 8, "vosk_model_size": "large",
        "db_path": "/tmp/custom.db",
    }))

    app = AppConfig.load(tmp_path)
    # GUI value wins for shared keys
    assert app.db_path == "/tmp/custom.db"
    assert app.engine.vosk_model_size == "large"
    assert app.gui.max_workers == 8
    # untouched engine section survives round-trip to the classic dict
    engine_dict = app.to_engine_dict()
    assert engine_dict["matching"]["confidence_threshold"] == 0.15
    assert engine_dict["stt"]["model_size"] == "large"


def test_config_validation_coerces_bad_enums():
    """Invalid enum values are coerced to safe defaults with a reported problem."""
    from config import AppConfig

    app = AppConfig()
    app.gui.theme = "Neon"
    app.gui.vosk_model_size = "gigantic"
    problems = app.validate()
    assert any("theme" in p for p in problems)
    assert app.gui.theme == "Dark"
    assert app.gui.vosk_model_size == "small"


def test_load_config_backward_compat():
    """fingerprint_core.load_config still returns the classic engine dict."""
    from fingerprint_core import load_config, load_typed_config
    from config import AppConfig

    cfg = load_config()
    assert isinstance(cfg, dict)
    assert "stt" in cfg and "matching" in cfg and "database" in cfg
    assert isinstance(load_typed_config(), AppConfig)
