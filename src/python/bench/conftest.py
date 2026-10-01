"""
Micro-benchmarks for E2 (lookup) and E7 (query), inside the Python process.
docs/TASKS.md, "Two measurement layers"; issue #60.

These operations take micro- to milliseconds: timed from outside through the
CLI, interpreter start-up would be most of the number.  So they are measured
here with pytest-benchmark, the course's tool for Python, and each result is
written as one SPEC.md §8 record so the benchmark runner can merge it with
the macro layer.

    pytest src/python/bench --benchmark-only --benchmark-json=results/micro-pytest.json

Skipped unless --benchmark-only is given (they are slow and need the mirror),
so the normal test suite and CI never run them.

Environment (all optional):
    BENCH_TIER          corpus tier, default 1000 (spec/corpus/manifest_<tier>.txt)
    BENCH_MIRROR        raw books, default infra/mirror
    BENCH_BACKENDS      default "json,sqlite" -- folder takes hours to build on WSL
    BENCH_MICRO_WORK    where the datalakes and indexes are built once and kept,
                        default ~/bench/work/.micro
    BENCH_MICRO_OUT     SPEC §8 records, default results/micro.jsonl
    BENCH_MACHINE_ID    default: host name

Protocol: the datalake and the index are built ONCE, outside the timed code;
warm-up rounds are discarded; the page cache is warm (a micro-benchmark
repeats the same operation, so it cannot be cold) -- the macro layer covers
cold-cache costs.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PY = HERE.parent
REPO = PY.parents[1]
for sub in ("core", "datalake", "datamart"):
    sys.path.insert(0, str(PY / sub))
sys.path.insert(0, str(PY))

TIER = int(os.environ.get("BENCH_TIER", "1000"))
MIRROR = Path(os.environ.get("BENCH_MIRROR", REPO / "infra" / "mirror"))
BACKENDS = [b for b in os.environ.get("BENCH_BACKENDS", "json,sqlite").split(",") if b]
WORK = Path(os.environ.get("BENCH_MICRO_WORK", "~/bench/work/.micro")).expanduser()
OUT = Path(os.environ.get("BENCH_MICRO_OUT", REPO / "results" / "micro.jsonl"))
SPEC_VERSION = (REPO / "spec" / "SPEC_VERSION").read_text().strip()
SEED = 42
# Same spread as the runner's --books-per-hour: the time layout gets one
# date/hour folder per 100 books, not a single flat folder (runner docstring).
BOOKS_PER_HOUR = int(os.environ.get("BENCH_BOOKS_PER_HOUR", "100"))


def pytest_collection_modifyitems(config, items):
    if config.getoption("benchmark_only", default=False):
        return
    skip = pytest.mark.skip(reason="micro-benchmarks: run with --benchmark-only")
    for item in items:
        if "bench" in Path(str(item.fspath)).parts:
            item.add_marker(skip)


def tier_ids() -> list[int]:
    path = REPO / "spec" / "corpus" / f"manifest_{TIER}.txt"
    return [int(x) for x in path.read_text().split() if x.strip()]


@pytest.fixture(scope="session")
def corpus():
    if not MIRROR.is_dir():
        pytest.skip(f"mirror not found at {MIRROR}")
    return tier_ids()


@pytest.fixture(scope="session")
def datalake(corpus):
    """layout -> workspace holding the tier, built once and kept."""
    from datalake.splitter import split_file
    from pipeline import make_storage

    built = {}
    for layout in ("time", "book", "hash"):
        ws = WORK / (f"datalake-{layout}-{TIER}" + (f"-h{BOOKS_PER_HOUR}" if layout == "time" else ""))
        marker = ws / ".complete"
        if not marker.exists():
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for k, book_id in enumerate(corpus):
                when = start + timedelta(hours=k // BOOKS_PER_HOUR)
                header, body = split_file(MIRROR / f"{book_id}.txt")
                make_storage(layout, ws, when).write(book_id, header, body)
            marker.write_text("ok\n")
        built[layout] = ws
    return built


@pytest.fixture(scope="session")
def indexes(corpus, datalake):
    """backend -> workspace holding the tier's positional index, built once."""
    from index_base import open_index
    from pipeline import make_storage
    from tokenizer import load_stopwords, tokenize

    stopwords = load_stopwords(REPO / "spec" / "stopwords_en.txt")
    source = make_storage("hash", datalake["hash"])
    built = {}
    for backend in BACKENDS:
        ws = WORK / f"index-{backend}-{TIER}"
        marker = ws / ".complete"
        if not marker.exists():
            index = open_index(backend, ws, positions=True)
            with index.batch() as batch:
                for book_id in corpus:
                    _, body_path = source.lookup(book_id)
                    body = (datalake["hash"] / body_path).read_text(encoding="utf-8")
                    batch.add_book(book_id, tokenize(body, stopwords))
            index.close()
            marker.write_text("ok\n")
        built[backend] = ws
    return built


def record(benchmark, *, experiment: str, layout=None, backend=None, workload=None,
           corpus_size: int | None = None) -> dict:
    """Write one SPEC §8 record from the benchmark's raw timings."""
    data = sorted(t * 1e6 for t in benchmark.stats.stats.data)  # seconds -> us
    q1, _, q3 = statistics.quantiles(data, n=4, method="inclusive")
    p95 = statistics.quantiles(data, n=20, method="inclusive")[18]
    rec = {
        "run_id": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"),
        "spec_version": SPEC_VERSION, "language": "python",
        "impl_version": os.environ.get("BENCH_IMPL_VERSION", "unknown"),
        "experiment": experiment, "datalake_layout": layout, "index_backend": backend,
        "positions": True if backend else None, "corpus_size": corpus_size or TIER, "workers": None,
        "batch_size": None, "repetition": None,
        "metric": "latency", "value": round(statistics.median(data), 3), "unit": "us",
        "aux": {"layer": "micro", "tool": "pytest-benchmark", "workload": workload,
                "q1": round(q1, 3), "q3": round(q3, 3), "iqr": round(q3 - q1, 3),
                "p95": round(p95, 3), "min": round(data[0], 3), "max": round(data[-1], 3),
                "rounds": len(data), "cache": "warm"},
        "machine_id": os.environ.get("BENCH_MACHINE_ID", socket.gethostname()),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    benchmark.extra_info.update(rec["aux"])
    return rec
