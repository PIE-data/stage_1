"""
`sqlite` backend — an embedded B-tree.  SPEC.md §6.3.

    datamarts/index.db

Schema, pragmas and query shapes come from §6.3 verbatim.  Two details worth
defending in §4 of the report:

  * `postings` is WITHOUT ROWID, so the row lives inside the primary-key
    B-tree itself.  For an access pattern that is always "give me the postings
    of this term", that halves the lookups: no hop from the index to a rowid
    table.
  * `df` is kept in its own `terms` table rather than computed with COUNT(*)
    at query time, because document frequency is read on every query and
    written once per book.

Positions are stored as a comma-separated TEXT column.  A separate row per
position would be the textbook normalisation and is the wrong choice here: it
multiplies the row count by roughly the token count and would turn the
comparison against the other two backends into a comparison of schemas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from index_base import IndexBackend, Posting, register

__all__ = ["SqliteIndex"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS terms (
    term  TEXT    PRIMARY KEY,
    df    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
    term      TEXT    NOT NULL,
    book_id   INTEGER NOT NULL,
    tf        INTEGER NOT NULL,
    positions TEXT,
    PRIMARY KEY (term, book_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_postings_book ON postings(book_id);
"""


class SqliteIndex(IndexBackend):
    def __init__(self, workspace, positions: bool = True) -> None:
        super().__init__(workspace, positions)
        self.path = Path(self.workspace) / "datamarts" / "index.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------------- storage

    def _store(self, book_id: int, postings: dict[str, tuple[int, list[int]]]) -> None:
        rows = [
            (
                term,
                book_id,
                tf,
                ",".join(str(p) for p in sorted(pos)) if self.positions else None,
            )
            for term, (tf, pos) in postings.items()
        ]

        # INSERT OR REPLACE gives idempotency for the terms this book contains
        # (invariant I4); see index_base on why no global cleanup is needed.
        self._conn.executemany(
            "INSERT OR REPLACE INTO postings (term, book_id, tf, positions)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )

        # df is recomputed only for the terms this book touched.  Doing it here
        # rather than at query time keeps reads cheap, which is what E7
        # measures.
        self._conn.executemany(
            "INSERT OR REPLACE INTO terms (term, df) VALUES"
            " (?, (SELECT COUNT(*) FROM postings WHERE term = ?))",
            [(term, term) for term, _ in postings.items()],
        )

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self.commit()
        self._conn.close()

    # ---------------------------------------------------------------- reading

    def terms(self) -> list[str]:
        # SQLite compares TEXT with BINARY collation by default, which is
        # UTF-8 byte order -- the ordering SPEC.md §7 asks for.  Spelled out
        # here because a port that sets a different collation would silently
        # reorder the canonical export.
        cur = self._conn.execute("SELECT term FROM terms ORDER BY term")
        return [row[0] for row in cur]

    def postings_of(self, term: str) -> list[Posting]:
        cur = self._conn.execute(
            "SELECT book_id, tf, positions FROM postings WHERE term = ? ORDER BY book_id",
            (term,),
        )
        out: list[Posting] = []
        for book_id, tf, positions in cur:
            pos = [int(p) for p in positions.split(",")] if positions else []
            out.append((book_id, tf, pos))
        return out

    def df(self, term: str) -> int:
        row = self._conn.execute("SELECT df FROM terms WHERE term = ?", (term,)).fetchone()
        return row[0] if row else 0


register("sqlite", SqliteIndex)
