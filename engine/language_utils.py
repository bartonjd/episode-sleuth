#!/usr/bin/env python3
"""Multi-language helpers for EpisodeSleuth.

Centralises everything language-related so the rest of the engine stays simple:

  * :func:`detect_language` - best-effort ISO 639-1 detection of a block of
    dialogue text, using the optional ``langdetect`` package. If the package is
    not installed (or detection fails) it returns ``None`` and the caller falls
    back to the configured Primary Language, so the feature is entirely optional
    and never breaks a build.
  * :func:`normalise_language` - turn a user-facing choice ("Auto-detect",
    "English", "Spanish", ...) or a raw code ("en", "es-ES") into a canonical
    two-letter ISO 639-1 code (or ``None`` for auto/unknown).
  * :func:`uses_metaphone` - whether Double-Metaphone phonetic encoding is
    appropriate for a language. Metaphone was designed for English spelling, so
    for other languages we fall back to raw (normalised) word matching, which is
    a better cross-STT signal there than misapplied English phonetics.

Nothing here raises: language handling is a convenience layer and must never be
able to break fingerprinting, matching or a library build.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

# Languages we expose in the UI dropdown, as (label, iso_code). "Auto-detect"
# and "Other" map to a special sentinel handled by ``normalise_language``.
SUPPORTED_LANGUAGES: List[Tuple[str, Optional[str]]] = [
    ("Auto-detect", None),
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Other", "other"),
]

# Reverse maps for tolerant parsing of whatever the config / caller hands us.
_LABEL_TO_CODE: Dict[str, Optional[str]] = {
    label.lower(): code for label, code in SUPPORTED_LANGUAGES
}
_KNOWN_CODES = {"en", "es", "fr", "de"}

# Only English currently benefits from Double-Metaphone; every other language
# (and the catch-all "other") uses raw normalised word matching instead.
_METAPHONE_LANGUAGES = {"en"}


def normalise_language(value: Optional[str]) -> Optional[str]:
    """Normalise a language label or code to a two-letter ISO 639-1 code.

    Accepts UI labels ("English", "Auto-detect"), full codes ("en-US") or bare
    codes ("es"). Returns the canonical two-letter code, or ``None`` for
    auto-detect / empty / unrecognised input so callers can decide the fallback.
    "other" is preserved as a sentinel meaning "a known-non-English language we
    do not special-case".
    """
    if not value:
        return None
    v = str(value).strip().lower()
    if not v or v in ("auto", "auto-detect", "autodetect"):
        return None
    if v in _LABEL_TO_CODE:
        code = _LABEL_TO_CODE[v]
        return code
    # Handle codes like "en", "en-US", "es_ES".
    base = v.replace("_", "-").split("-", 1)[0]
    if base in _KNOWN_CODES:
        return base
    if base == "other":
        return "other"
    # An unrecognised but plausible two-letter code: keep it, so a language we
    # do not special-case is still recorded rather than silently dropped.
    if len(base) == 2 and base.isalpha():
        return base
    return None


def uses_metaphone(language: Optional[str]) -> bool:
    """Return True if Double-Metaphone encoding suits ``language``.

    English (or unknown / ``None``, which we treat as English for backward
    compatibility) uses metaphone; all other languages fall back to raw word
    matching. Keeping the default as metaphone means existing English libraries
    behave exactly as before.
    """
    code = normalise_language(language)
    if code is None:
        return True  # unknown -> assume English (historical default)
    return code in _METAPHONE_LANGUAGES


def detect_language(text: str, default: Optional[str] = None,
                    min_chars: int = 40) -> Optional[str]:
    """Best-effort ISO 639-1 detection for a block of dialogue text.

    Returns a two-letter code, or ``default`` when detection is unavailable,
    the text is too short to be reliable, or anything goes wrong. Requires the
    optional ``langdetect`` package; if it is not installed this simply returns
    ``default`` (so builds work with or without it).
    """
    if not text or len(text.strip()) < min_chars:
        return default
    try:
        # Imported lazily; optional dependency.
        from langdetect import DetectorFactory, detect
        # Make detection deterministic across runs (langdetect is random by
        # default), so the same subtitle always tags the same language.
        DetectorFactory.seed = 0
        code = detect(text)
    except ImportError:
        logging.debug("langdetect not installed; skipping language detection")
        return default
    except Exception as exc:  # pragma: no cover - detection is best-effort
        logging.debug("Language detection failed: %s", exc)
        return default
    code = normalise_language(code)
    return code or default
