"""Tests for the unified typed configuration (config.AppConfig)."""
import json

from config import (
    AppConfig, EngineConfig, GuiConfig, CONFIG_FILE, GUI_CONFIG_FILE,
)


def test_appconfig_defaults():
    app = AppConfig()
    assert isinstance(app.engine, EngineConfig)
    assert isinstance(app.gui, GuiConfig)
    assert app.gui.theme == "Dark"
    assert app.engine.vosk_model_size == "small"


def test_appconfig_load_merges_configs(tmp_path):
    """config.json (engine) + gui_config.json (GUI) load and merge, with the
    GUI value winning for shared keys."""
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

    assert app.db_path == "/tmp/custom.db"          # GUI wins for shared key
    assert app.engine.vosk_model_size == "large"    # propagated to the engine
    assert app.gui.max_workers == 8
    # the engine dict round-trips the classic shape used by the matcher
    engine_dict = app.to_engine_dict()
    assert engine_dict["matching"]["confidence_threshold"] == 0.15
    assert engine_dict["stt"]["model_size"] == "large"


def test_appconfig_validation_invalid_theme():
    """An invalid theme is coerced to the default and reported as a problem."""
    app = AppConfig()
    app.gui.theme = "Neon"
    problems = app.validate()
    assert any("theme" in p.lower() for p in problems)
    assert app.gui.theme == "Dark"


def test_appconfig_validation_invalid_model_size():
    app = AppConfig()
    app.gui.vosk_model_size = "gigantic"
    problems = app.validate()
    assert any("model" in p.lower() for p in problems)
    assert app.gui.vosk_model_size == "small"


def test_appconfig_backward_compat(tmp_path):
    """An old-style config.json with no GUI file still loads with sane
    defaults."""
    (tmp_path / CONFIG_FILE).write_text(json.dumps({
        "stt": {"engine": "vosk"},
        "database": {"path": "fingerprints.db"},
        "matching": {"confidence_threshold": 0.20},
    }))
    # no gui_config.json present at all
    app = AppConfig.load(tmp_path)

    assert app.gui.theme == "Dark"                  # default kicks in
    assert app.engine.vosk_model_size == "small"
    engine_dict = app.to_engine_dict()
    assert engine_dict["matching"]["confidence_threshold"] == 0.20


def test_appconfig_save_roundtrip(tmp_path):
    """Saving then loading preserves the customised values."""
    app = AppConfig()
    app.gui.theme = "Light"
    app.gui.max_workers = 6
    app.db_path = "/data/fp.db"
    app.save(tmp_path)

    assert (tmp_path / CONFIG_FILE).exists()
    assert (tmp_path / GUI_CONFIG_FILE).exists()

    reloaded = AppConfig.load(tmp_path)
    assert reloaded.gui.theme == "Light"
    assert reloaded.gui.max_workers == 6
    assert reloaded.db_path == "/data/fp.db"
