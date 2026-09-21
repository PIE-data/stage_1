"""
Inverted-index backends: the shared contract.  SPEC.md §6.

Three storage strategies for exactly the same information:

    json    one big file            (datamarts/inverted_index.json)
    folder  one file per term       (datamarts/inverted_index/<BUCKET>/<term>.txt)
    sqlite  an embedded B-tree      (datamarts/index.db)

They MUST agree posting for posting.  The differences are cost, not content:
that is the whole point of the comparison in §5 of the report, and the reason
the same test suite runs against all three.

Definitions used throughout, so that the two ports can follow the same words:

    posting   (book_id, tf, positions) for one term in one book
    tf        how many times the term occurs in that book, AFTER filtering
    positions the token ordinals of those occurrences, ascending, assigned
              BEFORE filtering (SPEC.md §3.3) -- so they are not 0..tf-1
    df        how many distinct books contain the term

Ordering rules, which are part of the contract because the canonical export
depends on them (SPEC.md §7):

    terms      ascending by UTF-8 byte order
    postings   ascending by book_id
    positions  ascending

UTF-8 byte order happens to coincide with code-point order, so Python's plain
`sorted()` would do -- but it is spelled out with an explicit key here because
it does NOT coincide with the native string order in JavaScript, where strings
are UTF-16 and astral characters sort before U+E000..U+FFFF.  The Node port
must sort on Buffer.from(term), not on `<`.

Re-indexing a book that is already present replaces its postings and must not
duplicate them (invariant I4).  "Replace" means: for every term the book
contains, overwrite that book's posting.  No backend scans for terms the book
used to contain and no longer does, because a book's text is immutable once it
is in the datalake -- it is written once and never rewritten (§2.4).  The three
backends must agree on this, otherwise they would differ in cost as well as in
behaviour and the comparison would be measuring the difference.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path

__all__ = [
    "Posting",
    "IndexBackend",
    "build_postings",
    "term_sort_key",
    "atomic_write_bytes",
    "open_index",
    "BACKENDS",
]

# (book_id, tf, positions).  positions is empty when the index was built
# without --positions.
Posting = tuple[int, int, list[int]]


def term_sort_key(term: str) -> bytes:
    """UTF-8 byte order (SPEC.md §7).  See the module docstring."""
    return term.encode("utf-8")


def build_postings(tokens: list[tuple[str, int]]) -> dict[str, tuple[int, list[int]]]:
    """Fold one book's filtered token stream into term -> (tf, positions).

    `tokens` is what tokenizer.tokenize() returns: (term, position) pairs, the
    positions being pre-filter ordinals.  They arrive ascending and stay so.
    """
    acc: dict[str, list[int]] = defaultdict(list)
    for term, position in tokens:
        acc[term].append(position)
    return {term: (len(positions), positions) for term, positions in acc.items()}


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """SPEC.md §2.4: .part -> flush -> fsync -> rename -> fsync parent dir.

    A crash can then leave the old file or the new one, never a half-written
    one.  `os.replace` is atomic on POSIX and on Windows.

    NOTE: issue #1 (Manuel) owns the shared `datalake/atomic.py`.  This is a
    deliberate local copy so the two tracks do not block each other; switch to
    the shared one once it lands, in a commit that does nothing else.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")

    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())

    os.replace(tmp, path)

    # Directory fsync is what actually persists the rename.  It is not
    # available on Windows, where the rename is durable on its own.
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class IndexBackend(ABC):
    """What every backend must do.  Nothing here knows about storage."""

    def __init__(self, workspace: str | Path, positions: bool = True) -> None:
        self.workspace = Path(workspace)
        self.positions = positions
        self._batching = False

    # ---------------------------------------------------------------- writing

    def add_book(self, book_id: int, tokens: list[tuple[str, int]]) -> None:
        """Index one book.  Re-indexing replaces its postings (invariant I4)."""
        postings = build_postings(tokens)
        if not self.positions:
            postings = {term: (tf, []) for term, (tf, _) in postings.items()}
        self._store(book_id, postings)
        if not self._batching:
            self.commit()

    @abstractmethod
    def _store(self, book_id: int, postings: dict[str, tuple[int, list[int]]]) -> None:
        """Merge one book's postings into the backend's own state."""

    @abstractmethod
    def commit(self) -> None:
        """Make everything written so far durable."""

    def batch(self) -> "_Batch":
        """Defer commit until the end of the block.

        Used by `index --all` with --batch-size.  A SINGLE book update outside
        a batch must pay the backend's real update cost -- for `json` that
        means re-reading and rewriting the whole file -- because experiment E8
        measures exactly that.  Batching here is for bulk building, not a way
        to make the json backend look cheap.
        """
        return _Batch(self)

    # ---------------------------------------------------------------- reading

    @abstractmethod
    def terms(self) -> list[str]:
        """Every indexed term, ascending by UTF-8 byte order."""

    @abstractmethod
    def postings_of(self, term: str) -> list[Posting]:
        """Postings for one term, ascending by book_id.  [] if absent."""

    def df(self, term: str) -> int:
        return len(self.postings_of(term))

    def close(self) -> None:
        self.commit()

    def __enter__(self) -> "IndexBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Batch:
    def __init__(self, index: IndexBackend) -> None:
        self.index = index

    def __enter__(self) -> IndexBackend:
        self.index._batching = True
        return self.index

    def __exit__(self, *exc: object) -> None:
        self.index._batching = False
        self.index.commit()


def open_index(backend: str, workspace: str | Path, positions: bool = True) -> IndexBackend:
    """Factory matching the --index-backend flag (SPEC.md §1)."""
    # Imported here, not at module level: each backend module imports this
    # one, so a top-level import would be circular.  Repeat imports are free.
    import index_folder  # noqa: F401
    import index_json  # noqa: F401
    import index_sqlite  # noqa: F401

    if backend not in BACKENDS:
        raise ValueError(f"unknown index backend: {backend!r}")
    return BACKENDS[backend](workspace, positions)


# Populated at the bottom of the concrete modules to avoid a circular import.
BACKENDS: dict[str, type[IndexBackend]] = {}


def register(name: str, cls: type[IndexBackend]) -> None:
    BACKENDS[name] = cls
