"""
E3 (micro) -- detecting new books, per datalake layout.  Issue #60.

The macro E3 times `scan-new --since` from outside, where starting Python
(70-150 ms) is larger than the scan itself (a few ms at 1 000 books).  Here
the scan is timed inside the process, which isolates what the layout costs.

Setup, outside the timer and independent of the mirror: a synthetic datalake
of TIER old books plus NEW books ingested "now".  Detection never reads a
book's content, only names, folders and mtimes, so tiny files are a faithful
stand-in and make the 10 000 tier cheap to build.
  * time layout: old books in date/hour folders, BOOKS_PER_HOUR per folder
    from 2026-01-01T00, new books in the current hour's folder;
  * book, hash: old books with an mtime in 2026-01-01, new books with now;
  * every old book is marked indexed, so the answer is exactly the NEW ids.

Timed code = what `scan-new --since` does (pipeline.cmd_scan_new) minus
argument parsing and printing: list_new(since), then drop indexed ids.

Environment: BENCH_SCAN_TIERS (default "1000,10000"), BENCH_BOOKS_PER_HOUR
(default 100, the same default as the runner's --books-per-hour).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from conftest import WORK, record

TIERS = [int(x) for x in os.environ.get("BENCH_SCAN_TIERS", "1000,10000").split(",") if x]
BOOKS_PER_HOUR = int(os.environ.get("BENCH_BOOKS_PER_HOUR", "100"))
NEW = 50
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _plain_write(path, content) -> None:
    """Setup only: the real atomic_write fsyncs every file, which would make
    building 60 000 files take minutes and measures nothing here."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _build(layout: str, tier: int, now: datetime):
    import datalake.batch_storage as bs
    import datalake.book_storage as bk
    import datalake.time_storage as ts
    from pipeline import make_storage

    ws = WORK / f"scan-{layout}-{tier}-h{BOOKS_PER_HOUR}"
    shutil.rmtree(ws, ignore_errors=True)
    saved = {m: m.atomic_write for m in (bs, bk, ts) if hasattr(m, "atomic_write")}
    try:
        for m in saved:
            m.atomic_write = _plain_write
        old_ids = list(range(1, tier + 1))
        for k, book_id in enumerate(old_ids):
            when = START + timedelta(hours=k // BOOKS_PER_HOUR)
            header_rel, body_rel = make_storage(layout, ws, when).write(book_id, "h", "b")
            stamp = when.timestamp()
            for rel in (header_rel, body_rel):
                os.utime(ws / rel, (stamp, stamp))
        for book_id in range(tier + 1, tier + 1 + NEW):
            make_storage(layout, ws, now).write(book_id, "h", "b")
    finally:
        for m, fn in saved.items():
            m.atomic_write = fn
    (ws / "control").mkdir(parents=True, exist_ok=True)
    (ws / "control" / "indexed_books.txt").write_text("".join(f"{i}\n" for i in old_ids))
    return ws


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("layout", ["time", "book", "hash"])
def test_bench_scan_new(benchmark, layout, tier):
    from control_layer import StateTracker
    from pipeline import make_storage

    now = datetime.now(timezone.utc).replace(microsecond=0)
    since = now - timedelta(seconds=1)
    ws = _build(layout, tier, now)
    storage = make_storage(layout, ws)

    def scan():
        tracker = StateTracker(ws)
        present = set(storage.list_new(since))
        return sorted(i for i in present if not tracker.is_indexed(i))

    assert scan() == list(range(tier + 1, tier + 1 + NEW))  # right answer first
    benchmark.pedantic(scan, rounds=200, iterations=1, warmup_rounds=10)
    record(benchmark, experiment="E3_scan", layout=layout, corpus_size=tier,
           workload=f"{tier}+{NEW} books, {BOOKS_PER_HOUR}/h, synthetic")
