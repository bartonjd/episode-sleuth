#!/usr/bin/env python3
"""DEPRECATED shim - the reference-library builder moved to the cli package.

``create_fingerprint.py`` is now ``audio_fingerprint.cli.build_fingerprints``.
This file is kept so existing imports and invocations keep working:

    import create_fingerprint as cf          # still works
    python create_fingerprint.py --dir ...   # still works

Please migrate to::

    from audio_fingerprint.cli import build_fingerprints
    python -m audio_fingerprint.cli.build_fingerprints --dir ...
"""
from __future__ import annotations

import os
import sys
import warnings

# Make the cli / engine packages importable when run as a loose script.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Re-export the public builder API from its new home. Bare import so this works
# both as a loose script and inside the package.
from cli.build_fingerprints import (  # noqa: F401
    fingerprint_subtitle_file, run_directory, run_show, main,
)

warnings.warn(
    "create_fingerprint is deprecated; use 'audio_fingerprint.cli.build_fingerprints' "
    "or run 'python -m audio_fingerprint.cli.build_fingerprints'.",
    DeprecationWarning, stacklevel=2,
)


if __name__ == "__main__":
    sys.exit(main())
