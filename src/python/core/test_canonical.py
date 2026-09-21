"""Unit tests for the canonical export.  SPEC.md §7.

These tests describe the byte format clause by clause, because the format IS
the contract: a port is correct exactly when it reproduces these bytes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "src" / "python" / "datalake"))
sys.path.insert(0, str(REPO / "src" / "python" / "datamart"))

from canonical import canonical_bytes, canonical_sha256, export_canonical  # noqa: E402
from index_base import open_index  # noqa: E402

BACKENDS = ["json", "folder", "sqlite"]

BOOK_A = [("cat", 1), ("dog", 3), ("cat", 5), ("cat", 9)]
BOOK_B = [("dog", 0), ("bird", 4)]


@pytest.fixture
def index(tmp_path):
    idx = open_index("json", tmp_path)
    with idx.batch() as batched:
        batched.add_book(2, BOOK_A)
        batched.add_book(1, BOOK_B)
    yield idx
    idx.close()


def test_exact_bytes(index):
    assert canonical_bytes(index) == (
        b'{"bird":{"df":1,"postings":[[1,1,[4]]]},'
        b'"cat":{"df":1,"postings":[[2,3,[1,5,9]]]},'
        b'"dog":{"df":2,"postings":[[1,1,[0]],[2,1,[3]]]}}'
    )


def test_no_insignificant_whitespace(index):
    data = canonical_bytes(index)
    assert b", " not in data
    assert b'": ' not in data


def test_no_trailing_newline(index):
    assert not canonical_bytes(index).endswith(b"\n")


def test_terms_sorted_by_utf8_bytes(tmp_path):
    idx = open_index("json", tmp_path)
    with idx.batch() as batched:
        batched.add_book(1, [("zebra", 0), ("ωmega", 1), ("apple", 2), ("中文", 3)])
    keys = list(json.loads(canonical_bytes(idx).decode("utf-8")).keys())
    idx.close()
    assert keys == sorted(keys, key=lambda t: t.encode("utf-8"))
    assert keys[:2] == ["apple", "zebra"]       # ASCII first
    assert keys[-1] == "中文"                    # three-byte sequences last


def test_non_ascii_is_not_escaped(tmp_path):
    idx = open_index("json", tmp_path)
    with idx.batch() as batched:
        batched.add_book(1, [("don’t", 0)])
    data = canonical_bytes(idx)
    idx.close()
    assert "don’t".encode("utf-8") in data
    assert b"\\u2019" not in data


def test_pairs_instead_of_triples_without_positions(tmp_path):
    idx = open_index("json", tmp_path, positions=False)
    with idx.batch() as batched:
        batched.add_book(1, BOOK_A)
    assert canonical_bytes(idx) == (
        b'{"cat":{"df":1,"postings":[[1,3]]},"dog":{"df":1,"postings":[[1,1]]}}'
    )
    idx.close()


def test_export_writes_the_same_bytes(index, tmp_path):
    out = tmp_path / "out" / "canonical.json"
    data = export_canonical(index, out)
    assert out.read_bytes() == data


def test_deterministic_across_runs(index):
    assert canonical_sha256(index) == canonical_sha256(index)


def test_insertion_order_does_not_matter(tmp_path):
    first = open_index("json", tmp_path / "a")
    with first.batch() as batched:
        batched.add_book(1, BOOK_B)
        batched.add_book(2, BOOK_A)

    second = open_index("json", tmp_path / "b")
    with second.batch() as batched:
        batched.add_book(2, BOOK_A)
        batched.add_book(1, BOOK_B)

    assert canonical_sha256(first) == canonical_sha256(second)
    first.close()
    second.close()


def test_all_three_backends_produce_one_hash(tmp_path):
    """The whole point of §7, in miniature."""
    digests = set()
    for backend in BACKENDS:
        idx = open_index(backend, tmp_path / backend)
        with idx.batch() as batched:
            batched.add_book(2, BOOK_A)
            batched.add_book(1, BOOK_B)
        digests.add(canonical_sha256(idx))
        idx.close()
    assert len(digests) == 1, "the three backends disagree"
