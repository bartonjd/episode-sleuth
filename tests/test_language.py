"""Tests for the multi-language support feature.

All offline. Covers:
  * ``normalise_language`` label/code parsing and the "other" sentinel;
  * ``uses_metaphone`` keeping English (and unknown) on the historical
    metaphone default while other languages fall back to raw words;
  * ``detect_language`` degrading gracefully when the input is too short or
    ``langdetect`` is unavailable;
  * the ``media.language`` schema column round-tripping and back-filling;
  * ``FingerprintConfig.use_metaphone`` reflecting the configured language;
  * ``phonetic_token_stream`` storing raw words for a non-English config.
"""
import builtins

import pytest

from engine.language_utils import (
    SUPPORTED_LANGUAGES,
    detect_language,
    normalise_language,
    uses_metaphone,
)
from fingerprint_core import (
    FingerprintConfig,
    FingerprintDB,
    MediaInfo,
    phonetic_token_stream,
)


def _mk(**kw):
    base = dict(title="Matlock", media_type="tv", season=1, episode=1,
                source="s1e1.srt")
    base.update(kw)
    return MediaInfo(**base)


# ---------------------------------------------------------------------------
# normalise_language
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("Auto-detect", None),
    ("auto", None),
    ("English", "en"),
    ("english", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("en", "en"),
    ("en-US", "en"),
    ("es_ES", "es"),
    ("Other", "other"),
    ("other", "other"),
    ("!!!", None),
])
def test_normalise_language(value, expected):
    assert normalise_language(value) == expected


def test_unknown_two_letter_code_preserved():
    # A plausible but not specially-handled code is kept, not dropped.
    assert normalise_language("it") == "it"


def test_supported_languages_shape():
    # Every entry is a (label, code-or-None) pair and Auto-detect leads.
    assert SUPPORTED_LANGUAGES[0] == ("Auto-detect", None)
    for label, code in SUPPORTED_LANGUAGES:
        assert isinstance(label, str)
        assert code is None or isinstance(code, str)


# ---------------------------------------------------------------------------
# uses_metaphone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("language,expected", [
    (None, True),          # unknown -> historical English default
    ("en", True),
    ("English", True),
    ("es", False),
    ("fr", False),
    ("de", False),
    ("other", False),
])
def test_uses_metaphone(language, expected):
    assert uses_metaphone(language) is expected


# ---------------------------------------------------------------------------
# detect_language graceful fallback
# ---------------------------------------------------------------------------
def test_detect_language_short_text_returns_default():
    assert detect_language("hola", default="en") == "en"
    assert detect_language("", default=None) is None


def test_detect_language_without_langdetect(monkeypatch):
    """When langdetect is not importable, detection returns the default."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langdetect" or name.startswith("langdetect."):
            raise ImportError("simulated missing langdetect")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    long_text = "this is a long enough english sentence for detection " * 2
    assert detect_language(long_text, default="en") == "en"


def test_detect_language_identifies_spanish():
    # Only assert when langdetect is available; otherwise skip so the suite
    # still passes on a minimal install.
    pytest.importorskip("langdetect")
    text = ("hola buenos dias como estas espero que tengas un dia maravilloso "
            "vamos a la playa esta tarde para nadar en el mar")
    assert detect_language(text) == "es"


# ---------------------------------------------------------------------------
# media.language schema column
# ---------------------------------------------------------------------------
def test_language_column_roundtrips(tmp_path):
    db = FingerprintDB(str(tmp_path / "t.db"))
    mid = db.get_or_create_media(_mk(language="es"))
    info = db.media_info(mid)
    assert info.language == "es"


def test_language_column_defaults_none(tmp_path):
    db = FingerprintDB(str(tmp_path / "t.db"))
    mid = db.get_or_create_media(_mk())
    assert db.media_info(mid).language is None


def test_update_media_can_set_language(tmp_path):
    db = FingerprintDB(str(tmp_path / "t.db"))
    mid = db.get_or_create_media(_mk())
    assert db.update_media(mid, language="fr")
    assert db.media_info(mid).language == "fr"


# ---------------------------------------------------------------------------
# FingerprintConfig.use_metaphone + non-English token stream
# ---------------------------------------------------------------------------
def test_fingerprint_config_use_metaphone_default(engine_cfg):
    fp = FingerprintConfig.from_config(engine_cfg)
    # Default config has no language -> English/metaphone behaviour.
    assert fp.use_metaphone() is True


def test_fingerprint_config_use_metaphone_spanish(engine_cfg):
    import dataclasses
    fp = FingerprintConfig.from_config(engine_cfg)
    fp_es = dataclasses.replace(fp, language="es")
    assert fp_es.use_metaphone() is False


def test_non_english_stream_keeps_raw_words(engine_cfg):
    import dataclasses
    fp = FingerprintConfig.from_config(engine_cfg)
    fp_es = dataclasses.replace(fp, language="es")
    tokens = phonetic_token_stream("hola mundo amigo", fp_es)
    # Raw (lower-cased) words are stored, not metaphone codes.
    assert "hola" in tokens
    assert "mundo" in tokens


def test_english_stream_uses_metaphone(engine_cfg):
    fp = FingerprintConfig.from_config(engine_cfg)
    tokens = phonetic_token_stream("hello world", fp)
    # Metaphone codes are upper-case and not the raw words.
    assert "hello" not in tokens
    assert tokens  # non-empty
