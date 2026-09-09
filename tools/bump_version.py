#!/usr/bin/env python3
"""Bump the project version in a single place-aware pass.

Usage:
    python tools/bump_version.py 1.2.3

What it does:
  * Updates ``__version__`` in ``__init__.py``.
  * Updates ``version="..."`` in ``setup.py``.
  * If ``CHANGELOG.md`` has an ``## [Unreleased]`` section, converts it into a
    dated release heading for the new version and re-opens a fresh, empty
    ``## [Unreleased]`` section above it (Keep a Changelog style).

The script is intentionally conservative: it only rewrites the exact tokens it
recognizes and prints what it changed, so it is safe to run in CI or by hand.
"""
from __future__ import annotations

import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-.+][0-9A-Za-z.-]+)?$")


def _fail(msg: str) -> "int":
    sys.stderr.write("error: " + msg + "\n")
    return 2


def _replace_in_file(path: str, pattern: str, replacement: str) -> bool:
    """Replace the first regex match in ``path``. Return True if changed."""
    if not os.path.exists(path):
        sys.stderr.write("warning: %s not found, skipping\n" % path)
        return False
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    new_text, count = re.subn(pattern, replacement, text, count=1)
    if count == 0:
        sys.stderr.write("warning: no version token found in %s\n" % path)
        return False
    if new_text != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        return True
    return False


def _bump_changelog(path: str, version: str) -> bool:
    """Turn '## [Unreleased]' into a dated release heading for ``version``."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if "## [Unreleased]" not in text:
        return False
    today = datetime.date.today().isoformat()
    released = "## [Unreleased]\n\n## [%s] - %s" % (version, today)
    new_text = text.replace("## [Unreleased]", released, 1)
    if new_text != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        return True
    return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        return _fail("usage: python tools/bump_version.py X.Y.Z")
    version = argv[0].lstrip("v").strip()
    if not SEMVER_RE.match(version):
        return _fail("'%s' is not a valid semantic version (expected X.Y.Z)" % version)

    changed = []
    if _replace_in_file(
        os.path.join(HERE, "__init__.py"),
        r'__version__\s*=\s*"[^"]*"',
        '__version__ = "%s"' % version,
    ):
        changed.append("__init__.py")
    if _replace_in_file(
        os.path.join(HERE, "setup.py"),
        r'version\s*=\s*"[^"]*"',
        'version="%s"' % version,
    ):
        changed.append("setup.py")
    if _bump_changelog(os.path.join(HERE, "CHANGELOG.md"), version):
        changed.append("CHANGELOG.md")

    if changed:
        print("Bumped version to %s in: %s" % (version, ", ".join(changed)))
        print("Next steps:")
        print("  1. Review and edit the new CHANGELOG.md section.")
        print("  2. git commit -am 'Release %s'" % version)
        print("  3. git tag v%s && git push --tags" % version)
        return 0
    sys.stderr.write("nothing changed (version already %s?)\n" % version)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
