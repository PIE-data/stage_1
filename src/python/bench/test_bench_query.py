"""
E7 -- query cost, per index backend and workload.  docs/TASKS.md.

The four workloads of spec/queries/ (single term, AND of 2, AND of 3, absent
terms), 100 queries each, every query run 5 times in round-robin.  Timed code
= what `engine query` does after opening the index: normalise the terms
(SPEC.md §1.1) and intersect the posting lists.  The index is opened once,
outside the timer.
"""

from __future__ import annotations

import itertools

import pytest

from conftest import BACKENDS, REPO, record

WORKLOADS = ["single", "and2", "and3", "absent"]
PASSES = 5


@pytest.mark.parametrize("workload", WORKLOADS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_bench_query(benchmark, indexes, backend, workload):
    from cli import run_query
    from index_base import open_index
    from tokenizer import load_stopwords, tokenize

    # cli.query_terms reads the stop-word file on every call (fine for one
    # query per process); here it is loaded once, so the timer sees the query.
    stopwords = load_stopwords(REPO / "spec" / "stopwords_en.txt")

    def query_terms(raw: str) -> list[str]:
        return list(dict.fromkeys(term for term, _ in tokenize(raw, stopwords)))

    lines = [line.strip() for line in
             (REPO / "spec" / "queries" / f"{workload}.txt").read_text().splitlines()
             if line.strip()]
    queries = itertools.cycle(lines)
    index = open_index(backend, indexes[backend], positions=True)

    def one_query():
        return run_query(index, query_terms(next(queries)), "and")

    try:
        benchmark.pedantic(one_query, rounds=len(lines) * PASSES, iterations=1,
                           warmup_rounds=20)
    finally:
        index.close()
    record(benchmark, experiment="E7_query", backend=backend, workload=workload)
