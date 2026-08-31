#!/usr/bin/env python3
"""Shared logging configuration for the CLI and GUI entry points.

One place to configure the root logger so console output looks the same whether
you run the batch identifier from the command line or launch the desktop GUI.
This intentionally installs no Qt handlers - the GUI adds its own log bridge on
top of this base configuration (see gui/logging_bridge.py).
"""
from __future__ import annotations

import logging
from typing import Union

_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: Union[int, str] = logging.INFO) -> None:
    """Configure the root logger with a simple, consistent console format.

    ``level`` may be a logging level constant (e.g. ``logging.INFO``) or its
    name as a string (e.g. ``"INFO"``, ``"WARNING"``). Safe to call more than
    once; the format is (re)applied via ``force=True``.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT,
                        force=True)
