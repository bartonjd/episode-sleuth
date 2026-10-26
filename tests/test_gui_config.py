"""Tests for the per-user GUI config (gui_config.py) and the SpinBox priming.

These cover the bug the user reported on Windows: settings were written next to
the program (a read-only location under an MSIX install) so nothing persisted,
and numeric fields showed up blank. The tests verify the writable per-user
path, first-run creation, atomic save, legacy migration, round-trip of the
keys, type coercion of bad values, and that every numeric box on the Identify
and Settings pages shows its value (a non-empty line edit) when rendered
offscreen.
"""
import json
import os
import sys

import pytest

# Import the gui package up front so the lazy ``from gui.path_utils import
# native_path`` inside gui_config never triggers a fresh ``import gui`` while a
# test has monkeypatched sys.platform to "win32" (that would send PySide6's
# __init__ down its Windows-only os.add_dll_directory branch and crash on Linux).
import gui.path_utils  # noqa: E402,F401
import gui_config
from gui_config import DEFAULTS, GuiConfig


# ---------------------------------------------------------------------------
# Config path resolution (Windows APPDATA + Linux XDG)
# ---------------------------------------------------------------------------
def test_windows_appdata_path(monkeypatch):
    monkeypatch.delenv(gui_config.CONFIG_DIR_ENV, raising=False)
    monkeypatch.setattr(gui_config.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\barto\AppData\Roaming")
    directory = gui_config.gui_config_dir()
    assert directory.endswith("EpisodeSleuth")
    assert "Roaming" in directory
    assert gui_config.gui_config_path().endswith("gui_config.json")


def test_windows_appdata_fallback_to_home(monkeypatch):
    monkeypatch.delenv(gui_config.CONFIG_DIR_ENV, raising=False)
    monkeypatch.setattr(gui_config.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    directory = gui_config.gui_config_dir()
    assert directory.endswith("EpisodeSleuth")
    assert os.path.expanduser("~").rstrip("/\\") in directory


def test_linux_xdg_path(monkeypatch, tmp_path):
    monkeypatch.delenv(gui_config.CONFIG_DIR_ENV, raising=False)
    monkeypatch.setattr(gui_config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = gui_config.gui_config_dir()
    assert directory.endswith("episodesleuth")
    assert str(tmp_path) in directory


def test_linux_xdg_default(monkeypatch):
    monkeypatch.delenv(gui_config.CONFIG_DIR_ENV, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(gui_config.sys, "platform", "linux")
    directory = gui_config.gui_config_dir()
    assert directory.endswith(os.path.join(".config", "episodesleuth"))


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(tmp_path))
    assert gui_config.gui_config_dir() == gui_config._native_path(str(tmp_path))


# ---------------------------------------------------------------------------
# First-run, atomic save, round-trip
# ---------------------------------------------------------------------------
def test_first_run_creates_file(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "cfgdir"
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(cfg_dir))
    path = gui_config.gui_config_path()
    assert not os.path.exists(path)
    GuiConfig()  # construction loads -> first run -> save
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["samples_per_file"] == DEFAULTS["samples_per_file"]


def test_save_is_atomic_no_tmp_left(monkeypatch, tmp_path):
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(tmp_path))
    cfg = GuiConfig()
    cfg.set("max_workers", 9)
    cfg.save()
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".gui_config_")]
    assert leftovers == []
    assert os.path.exists(gui_config.gui_config_path())


def test_round_trip_all_keys(monkeypatch, tmp_path):
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(tmp_path))
    cfg = GuiConfig()
    cfg.update(
        max_workers=6,
        samples_per_file=7,
        sample_length=15.0,
        review_confidence=0.40,
        vosk_model_size="large",
        scan_depth=-1,
        media_extensions=".mkv,.mp4",
        primary_language="English",
    )
    cfg.save()
    again = GuiConfig()
    assert again.get("max_workers") == 6
    assert again.get("samples_per_file") == 7
    assert again.get("sample_length") == 15.0
    assert again.get("review_confidence") == 0.40
    assert again.get("vosk_model_size") == "large"
    assert again.get("scan_depth") == -1
    assert again.get("media_extensions") == ".mkv,.mp4"
    assert again.get("primary_language") == "English"


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------
def test_legacy_migration(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy_gui_config.json"
    legacy.write_text(json.dumps({"max_workers": 11, "vosk_model_size": "large"}),
                      encoding="utf-8")
    monkeypatch.setattr(gui_config, "LEGACY_GUI_CONFIG_PATH", str(legacy))
    new_dir = tmp_path / "new"
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(new_dir))
    cfg = GuiConfig()
    assert cfg.get("max_workers") == 11
    assert cfg.get("vosk_model_size") == "large"
    # the new per-user file should now exist (first-run save after migration)
    assert os.path.exists(gui_config.gui_config_path())


# ---------------------------------------------------------------------------
# Type coercion of bad values
# ---------------------------------------------------------------------------
def test_type_coercion_of_bad_values(monkeypatch, tmp_path):
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(tmp_path))
    path = os.path.join(str(tmp_path), "gui_config.json")
    bad = {
        "samples_per_file": "9",      # str -> int
        "max_workers": None,          # null -> fallback
        "sample_length": "abc",       # junk -> fallback
        "review_confidence": 5.0,     # out of range -> clamp
        "scan_depth": "7",            # invalid depth -> fallback
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    cfg = GuiConfig()
    assert cfg.get("samples_per_file") == 9
    assert cfg.get("max_workers") == DEFAULTS["max_workers"]
    assert cfg.get("sample_length") == DEFAULTS["sample_length"]
    assert cfg.get("review_confidence") == 0.95     # clamped to upper bound
    assert cfg.get("scan_depth") == DEFAULTS["scan_depth"]


# ---------------------------------------------------------------------------
# Non-empty SpinBox text on both pages (offscreen)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


class _FakeWindow:
    """Minimal stand-in: the pages only read ``gui_cfg`` during construction."""

    def __init__(self, cfg):
        self.gui_cfg = cfg


def _seeded_cfg(monkeypatch, tmp_path):
    monkeypatch.setenv(gui_config.CONFIG_DIR_ENV, str(tmp_path))
    path = os.path.join(str(tmp_path), "gui_config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "max_workers": 6,
            "samples_per_file": 7,
            "sample_length": 15.0,
            "review_confidence": 0.40,
            "vosk_model_size": "large",
        }, fh)
    return GuiConfig()


def test_identify_spinboxes_not_blank(qapp, monkeypatch, tmp_path):
    cfg = _seeded_cfg(monkeypatch, tmp_path)
    from gui.pages.identify import IdentifyInterface
    page = IdentifyInterface(_FakeWindow(cfg))
    for spin in (page.samples_spin, page.samplelen_spin,
                 page.review_spin, page.workers_spin):
        assert spin.lineEdit().text().strip() != ""
    assert page.samples_spin.value() == 7
    assert page.workers_spin.value() == 6
    assert abs(page.samplelen_spin.value() - 15.0) < 1e-6
    assert abs(page.review_spin.value() - 0.40) < 1e-6
    page.deleteLater()


def test_settings_spinbox_not_blank(qapp, monkeypatch, tmp_path):
    cfg = _seeded_cfg(monkeypatch, tmp_path)
    from gui.pages.settings import SettingsInterface
    page = SettingsInterface(_FakeWindow(cfg))
    assert page.workers_spin.lineEdit().text().strip() != ""
    assert page.workers_spin.value() == 6
    # the config-location label is populated with the per-user path
    assert page.config_path_label.text().strip() != ""
    page.deleteLater()
