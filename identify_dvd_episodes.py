#!/usr/bin/env python3
"""DEPRECATED shim - the engine now lives in the ``engine`` / ``cli`` packages.

This module used to contain both the core matching engine and the CLI. In
Phase 3b it was split:

    core engine   -> audio_fingerprint.engine  (types, discovery, scoring,
                                                 matcher, batch)
    command line  -> audio_fingerprint.cli.identify

This file is kept only so old imports keep working:

    from identify_dvd_episodes import identify_one, FileResult   # still works

Please migrate to the new locations, e.g.::

    from engine import identify_one, FileResult, batch_identify
    from engine.batch import write_csv, write_json

Running ``python identify_dvd_episodes.py ...`` still works and simply forwards
to ``audio_fingerprint.cli.identify:main``.
"""
from __future__ import annotations

import os
import sys
import warnings

# Make the flat engine modules importable when run as a loose script.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Re-export the public engine API from its new home.
# The CLI entry point (argparse + main) moved to cli/identify.py. Use a bare
# import so this works both as a loose script and inside the package.
from cli.identify import main, parse_points  # noqa: F401
from engine.batch import batch_identify, write_csv, write_json  # noqa: F401
from engine.discovery import (  # noqa: F401
    build_suggested_filename,
    discover_media,
    episode_id_str,
    sanitize_filename,
)
from engine.matcher import (  # noqa: F401
    _ffmpeg_extract,
    _log,
    _probe_duration,
    _subprocess_flags,
    identify_one,
    sample_windows,
    transcribe_samples,
)
from engine.scoring import (  # noqa: F401
    _adaptive_review_threshold,
    _build_weighted_query,
    _load_candidate_streams,
    _norm_title,
    _time_weight,
    apply_metadata_boosts,
    run_fuzzy_stage,
    score_matches,
)
from engine.types import (  # noqa: F401
    AUDIO_EXTS,
    DEFAULT_SAMPLE_POINTS,
    MEDIA_EXTS,
    VIDEO_EXTS,
    EpisodeGuess,
    FileResult,
)

warnings.warn(
    "identify_dvd_episodes is deprecated; import from 'engine' (engine.identify_one, "
    "engine.batch.batch_identify) or run 'python -m audio_fingerprint.cli.identify'.",
    DeprecationWarning, stacklevel=2,
)


if __name__ == "__main__":
    raise SystemExit(main())
