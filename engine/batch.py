#!/usr/bin/env python3
"""Batch orchestration and result writers.

``batch_identify`` runs ``identify_one`` over a list of files - optionally in
parallel with a thread pool - and returns the results in input order. It is the
single entry point the CLI (and any other consumer) uses to identify a folder of
rips; the CLI layer keeps only argument parsing and console/summary formatting.
"""
from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from .types import FileResult
from .matcher import identify_one


def batch_identify(media: List[str], db_path: str, fp_cfg, cfg: dict, args,
                   transcriber, runtimes: Optional[dict] = None,
                   workers: int = 4,
                   progress: Optional[Callable[[int, int, str], None]] = None
                   ) -> List[FileResult]:
    """Identify every existing file in ``media`` and return ordered results.

    Files are processed with a thread pool (``workers`` > 1) or sequentially
    (``workers`` == 1). ``progress``, if given, is called ``progress(done,
    total, path)`` after each file completes. The returned list is sorted back
    into the original ``media`` order regardless of completion order.
    """
    results: List[FileResult] = []
    existing = [p for p in media if os.path.exists(p)]
    total = len(existing)
    done = 0

    if workers <= 1:
        for path in existing:
            results.append(identify_one(path, db_path, fp_cfg, cfg, args,
                                        transcriber, runtimes))
            done += 1
            if progress is not None:
                progress(done, total, path)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(identify_one, path, db_path, fp_cfg, cfg, args,
                                transcriber, runtimes): path
                    for path in existing}
            for fut in as_completed(futs):
                results.append(fut.result())
                done += 1
                if progress is not None:
                    progress(done, total, futs[fut])

    # keep output order stable (input order) regardless of completion order
    order = {p: i for i, p in enumerate(existing)}
    results.sort(key=lambda r: order.get(r.path, 0))
    return results


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def write_csv(results: List[FileResult], path: str) -> None:
    fields = ["filename", "episode_id", "title", "episode_title",
              "name_status", "suggested_filename", "confidence", "agreement",
              "method", "duration_s", "needs_review", "notes", "elapsed_s"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(r.to_row())


def write_json(results: List[FileResult], path: str) -> None:
    payload = [r.to_row() for r in results]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
