"""
E2 -- lookup cost, per datalake layout.  docs/TASKS.md (experiment matrix).

1 000 seeded lookups (seed 42, drawn with replacement from the tier), each
resolving the book through the layout's own rules and reading its body --
exactly what `engine lookup` does (SPEC.md §1.2, §4.1).  One round = one
lookup, so the distribution gives p50 / p95 over the 1 000 lookups.
"""

from __future__ import annotations

import itertools
import random

import pytest

from conftest import SEED, record

LOOKUPS = 1000


@pytest.mark.parametrize("layout", ["time", "book", "hash"])
def test_bench_lookup(benchmark, datalake, corpus, layout):
    from pipeline import make_storage

    ws = datalake[layout]
    storage = make_storage(layout, ws)
    ids = itertools.cycle(random.Random(SEED).choices(corpus, k=LOOKUPS))

    def lookup_and_read():
        header_path, body_path = storage.lookup(next(ids))
        return len((ws / body_path).read_bytes())

    benchmark.pedantic(lookup_and_read, rounds=LOOKUPS, iterations=1, warmup_rounds=50)
    record(benchmark, experiment="E2_lookup", layout=layout)
