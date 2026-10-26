#!/usr/bin/env python3
"""Tests for band-based sample-point selection (skip intro / end credits).

``build_sample_points`` must keep every chosen position inside the informative
middle of the file - away from the opening recap/titles and the closing credits
- while remaining backward compatible with explicit point lists elsewhere.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.matcher import (  # noqa: E402
    DEFAULT_SAMPLE_MARGIN,
    build_sample_points,
    sample_windows,
)


def test_default_points_skip_intro_and_credits():
    """Every default point sits within the central 80% band (0.10-0.90)."""
    pts = build_sample_points(5)
    assert pts == [0.10, 0.30, 0.50, 0.70, 0.90]
    assert all(DEFAULT_SAMPLE_MARGIN <= p <= 1.0 - DEFAULT_SAMPLE_MARGIN
               for p in pts)


def test_points_never_touch_first_or_last_fraction():
    """No chosen point falls in the first or last margin for various n."""
    for n in range(1, 16):
        pts = build_sample_points(n)
        assert len(pts) == n
        assert min(pts) >= DEFAULT_SAMPLE_MARGIN - 1e-9
        assert max(pts) <= 1.0 - DEFAULT_SAMPLE_MARGIN + 1e-9
        # strictly increasing (evenly spread, no duplicates) for n > 1
        if n > 1:
            assert all(b > a for a, b in zip(pts, pts[1:]))


def test_single_point_is_centred():
    assert build_sample_points(1) == [0.5]


def test_custom_margin_widens_or_narrows_band():
    wide = build_sample_points(3, margin_start=0.2, margin_end=0.2)
    assert wide[0] >= 0.2 - 1e-9 and wide[-1] <= 0.8 + 1e-9
    # asymmetric margins are honoured independently
    asym = build_sample_points(2, margin_start=0.05, margin_end=0.25)
    assert abs(asym[0] - 0.05) < 1e-6
    assert abs(asym[-1] - 0.75) < 1e-6


def test_pathological_margins_fall_back_to_safe_band():
    """Absurd margins must not crash or produce out-of-range points."""
    pts = build_sample_points(4, margin_start=0.9, margin_end=0.9)
    assert all(0.0 <= p <= 1.0 for p in pts)
    assert len(pts) == 4


def test_sample_windows_consume_band_points():
    """The band points turn into in-bounds windows on a real duration."""
    duration = 1200.0  # 20 minutes
    pts = build_sample_points(5)
    windows = sample_windows(duration, pts, 12.0)
    assert windows, "expected concrete windows"
    for start, length in windows:
        assert 0.0 <= start <= duration
        assert start + length <= duration + 1e-6
        # first window must start after the intro margin (minus one sample len)
    assert windows[0][0] >= duration * DEFAULT_SAMPLE_MARGIN - 12.0
