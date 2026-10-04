"""
E13 -- metadata queries: the query set of SPEC.md §5.4 on the SQLite
metadata datamart.  The brief's "query time" and, run at several tiers
(BENCH_TIER=100, 1000, 10000), its scalability.

    Q1  SELECT * FROM books WHERE author = ?
    Q2  SELECT body_path FROM books WHERE book_id = ?
    Q3  SELECT * FROM books WHERE title LIKE ? || '%'
    Q4  SELECT language, COUNT(*) FROM books GROUP BY language

Parameters come from spec/queries/metadata_params.txt (fixed seed, generated
once from the 1000-book tier by tools/make_metadata_params.py), so every tier
runs the same workload: at a smaller tier some authors and ids simply match
nothing, which is a valid -- and realistic -- lookup.  Each parameter runs
PASSES times in round-robin; Q4 has no parameter.

One extra workload, Q3_range, is NOT in the spec: the same title prefix as a
range `title >= ? AND title < ? || char(0x10FFFF)`.  SQLite cannot use the
title index for `LIKE` when the column has the default BINARY collation and
LIKE is case-insensitive (its default), so Q3 scans the whole table; the
range form can seek the index.  Running both shows what the spec's Q3 costs
and why.  The query plan of every workload is stored with the record.

Why only Python: the queries are plain SQL executed by the SQLite engine; the
language only passes parameters and reads rows.  Building the database is
where the languages differ, and that is measured for all three by the runner
(E12), which also checks that the three build identical rows.
"""

from __future__ import annotations

import itertools
import sqlite3
from datetime import datetime, timezone

import pytest

from conftest import MIRROR, REPO, TIER, WORK, record

PARAMS = REPO / "spec" / "queries" / "metadata_params.txt"
PASSES = 5

QUERIES = {
    "Q1": "SELECT * FROM books WHERE author = ?",
    "Q2": "SELECT body_path FROM books WHERE book_id = ?",
    "Q3": "SELECT * FROM books WHERE title LIKE ? || '%'",
    "Q3_range": "SELECT * FROM books WHERE title >= ? AND title < ? || char(1114111)",
    "Q4": "SELECT language, COUNT(*) FROM books GROUP BY language",
}


def load_params() -> dict[str, list[str]]:
    if not PARAMS.exists():
        pytest.skip(f"{PARAMS.relative_to(REPO)} not found (generate it with "
                    "tools/make_metadata_params.py)")
    params: dict[str, list[str]] = {}
    for line in PARAMS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            params.setdefault(key, []).append(value)
    return params


@pytest.fixture(scope="session")
def metadata_db(corpus):
    """The tier's metadata database, built once (outside the timer, kept
    between runs) from a hash-layout datalake of the tier.  Only the hash
    layout is built here, not the three of the `datalake` fixture: at the
    10 000-book tier the other two would cost minutes and gigabytes for
    nothing -- the queries do not depend on the layout."""
    from datalake.splitter import split_file
    from datamart.metadata import MetadataStore, build_metadata_record
    from pipeline import make_storage

    lake = WORK / f"datalake-hash-{TIER}"
    if not (lake / ".complete").exists():
        storage = make_storage("hash", lake)
        for book_id in corpus:
            header, body = split_file(MIRROR / f"{book_id}.txt")
            storage.write(book_id, header, body)
        (lake / ".complete").write_text("ok\n")

    ws = WORK / f"metadata-{TIER}"
    marker = ws / ".complete"
    if not marker.exists():
        source = make_storage("hash", lake)
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = []
        for book_id in corpus:
            header_path, body_path = source.lookup(book_id)
            records.append(build_metadata_record(book_id, lake, header_path, body_path, stamp))
        with MetadataStore(ws) as store:
            store.upsert(records, batch_size=500)
        marker.write_text("ok\n")
    return ws / "datamarts" / "metadata.db"


def arguments(workload: str, params: dict[str, list[str]]) -> list[tuple]:
    if workload == "Q2":
        return [(int(v),) for v in params["Q2"]]
    if workload == "Q3_range":
        return [(v, v) for v in params["Q3"]]
    if workload == "Q4":
        return [()]
    return [(v,) for v in params[workload]]


@pytest.mark.parametrize("workload", list(QUERIES))
def test_bench_metadata_query(benchmark, metadata_db, workload):
    params = load_params()
    args = arguments(workload, params)
    sql = QUERIES[workload]
    con = sqlite3.connect(f"file:{metadata_db}?mode=ro", uri=True)
    try:
        plan = " | ".join(row[-1] for row in
                          con.execute("EXPLAIN QUERY PLAN " + sql, args[0]).fetchall())
        matched = sum(len(con.execute(sql, a).fetchall()) for a in args)
        cycle = itertools.cycle(args)

        def one_query():
            return con.execute(sql, next(cycle)).fetchall()

        rounds = len(args) * PASSES if workload != "Q4" else 200
        benchmark.pedantic(one_query, rounds=rounds, iterations=1, warmup_rounds=20)
    finally:
        con.close()
    record(benchmark, experiment="E13_metadata_query", workload=workload,
           extra={"plan": plan, "rows_matched": matched, "params": len(args)})
