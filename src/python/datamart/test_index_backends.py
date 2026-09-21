"""
One test suite, run against all three index backends.  SPEC.md §6.

If json, folder and sqlite ever disagree here, the canonical export cannot
produce one hash and the whole comparison in §5 collapses -- so these tests
matter more than their size suggests.  They are also the tests the Node and Go
ports should mirror.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from index_base import open_index  # noqa: E402
from index_folder import bucket_of, decode_term, encode_term  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "spec" / "golden"

BACKENDS = ["json", "folder", "sqlite"]

# (term, position) pairs as tokenizer.tokenize() returns them.  Note the
# positions are NOT 0..n: they are pre-filter ordinals, so a gap means a
# stop word was dropped there (SPEC.md §3.3).
BOOK_A = [("cat", 1), ("dog", 3), ("cat", 5), ("cat", 9)]
BOOK_B = [("dog", 0), ("bird", 4)]


@pytest.fixture(params=BACKENDS)
def index(request, tmp_path):
    idx = open_index(request.param, tmp_path)
    yield idx
    idx.close()


# ------------------------------------------------------------- basic contract


def test_tf_and_positions(index):
    index.add_book(1, BOOK_A)
    assert index.postings_of("cat") == [(1, 3, [1, 5, 9])]
    assert index.postings_of("dog") == [(1, 1, [3])]


def test_absent_term(index):
    index.add_book(1, BOOK_A)
    assert index.postings_of("elephant") == []
    assert index.df("elephant") == 0


def test_postings_sorted_by_book_id(index):
    index.add_book(7, BOOK_B)
    index.add_book(2, BOOK_A)
    assert [p[0] for p in index.postings_of("dog")] == [2, 7]


def test_df_counts_books_not_occurrences(index):
    index.add_book(1, BOOK_A)      # cat appears three times in one book
    index.add_book(2, BOOK_B)
    assert index.df("cat") == 1
    assert index.df("dog") == 2


def test_terms_are_utf8_byte_sorted(index):
    index.add_book(1, [("zebra", 0), ("apple", 1), ("Ωmega".lower(), 2), ("banana", 3)])
    terms = index.terms()
    assert terms == sorted(terms, key=lambda t: t.encode("utf-8"))
    # Non-ASCII sorts after every ASCII term, which is what byte order means.
    assert terms[-1] == "ωmega"


def test_reindexing_is_idempotent(index):
    index.add_book(1, BOOK_A)
    before = index.postings_of("cat")
    index.add_book(1, BOOK_A)
    assert index.postings_of("cat") == before
    assert index.df("cat") == 1


def test_reindexing_replaces_rather_than_appends(index):
    index.add_book(1, BOOK_A)
    index.add_book(1, [("cat", 2)])
    assert index.postings_of("cat") == [(1, 1, [2])]


def test_without_positions(tmp_path):
    for backend in BACKENDS:
        idx = open_index(backend, tmp_path / backend, positions=False)
        idx.add_book(1, BOOK_A)
        assert idx.postings_of("cat") == [(1, 3, [])], backend
        idx.close()


def test_batch_defers_the_write(tmp_path):
    idx = open_index("json", tmp_path)
    with idx.batch() as batched:
        batched.add_book(1, BOOK_A)
        assert not idx.path.exists()
    assert idx.path.exists()
    idx.close()


# ------------------------------------------------------- folder-specific rules


@pytest.mark.parametrize(
    "term,expected",
    [
        ("cat", "cat"),
        ("don't", "don%27t"),
        ("42", "42"),
        ("ωmega", "%CF%89mega"),
        ("中文", "%E4%B8%AD%E6%96%87"),
    ],
)
def test_term_encoding_round_trip(term, expected):
    assert encode_term(term) == expected
    assert decode_term(expected) == term


def test_encoding_separates_case_insensitive_collisions():
    """"Apple" and "apple" must not become the same filename on NTFS/APFS."""
    assert encode_term("Apple") != encode_term("apple")


@pytest.mark.parametrize(
    "term,bucket",
    [("cat", "C"), ("42", "_"), ("ωmega", "_"), ("中文", "_")],
)
def test_bucket_rule(term, bucket):
    assert bucket_of(term) == bucket


def test_folder_writes_one_file_per_term(tmp_path):
    idx = open_index("folder", tmp_path)
    idx.add_book(1, BOOK_A)
    idx.close()
    files = sorted(p.name for p in (tmp_path / "datamarts" / "inverted_index").glob("*/*.txt"))
    assert files == ["cat.txt", "dog.txt"]


def test_folder_reindex_writes_nothing(tmp_path):
    """Invariant I4: re-running on a complete corpus performs zero writes."""
    idx = open_index("folder", tmp_path)
    idx.add_book(1, BOOK_A)
    path = idx.path_of("cat")
    before = path.stat().st_mtime_ns

    idx.add_book(1, BOOK_A)
    assert path.stat().st_mtime_ns == before
    idx.close()


# ------------------------------------------- the three agree on a real corpus


def golden_books(limit: int = 3) -> list[Path]:
    books = [p for p in GOLDEN.glob("*.txt") if p.stem.isdigit()]
    return sorted(books, key=lambda p: p.stat().st_size)[:limit]


@pytest.mark.skipif(not golden_books(), reason="golden fixture not present")
def test_all_three_backends_agree_on_golden_books(tmp_path):
    sys.path.insert(0, str(REPO / "src" / "python" / "core"))
    sys.path.insert(0, str(REPO / "src" / "python" / "datalake"))
    from splitter import split_file
    from tokenizer import load_stopwords, tokenize

    stopwords = load_stopwords(REPO / "spec" / "stopwords_en.txt")
    books = []
    for path in golden_books():
        _, body = split_file(path)
        books.append((int(path.stem), tokenize(body, stopwords)))

    results = {}
    for backend in BACKENDS:
        idx = open_index(backend, tmp_path / backend)
        with idx.batch() as batched:
            for book_id, tokens in books:
                batched.add_book(book_id, tokens)
        results[backend] = (idx.terms(), {t: idx.postings_of(t) for t in idx.terms()})
        idx.close()

    reference_terms, reference_postings = results["json"]
    assert len(reference_terms) > 100, "the corpus produced suspiciously few terms"

    for backend in BACKENDS[1:]:
        terms, postings = results[backend]
        assert terms == reference_terms, f"{backend}: term list differs from json"
        assert postings == reference_postings, f"{backend}: postings differ from json"
