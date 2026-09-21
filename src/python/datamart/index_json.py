"""
`json` backend — one monolithic file.  SPEC.md §6.1.

    datamarts/inverted_index.json

Stored shape, deliberately the same as the canonical export (§7), so that
export-canonical is a re-serialisation rather than a translation:

    {"<term>":{"df":<int>,"postings":[[<id>,<tf>,[<pos>,…]],…]},…}

Without positions the posting triples become pairs: [<id>,<tf>].

Updating a single book loads, merges and rewrites the WHOLE file.  That O(N)
cost is not an oversight, it is what experiment E8 measures; §6.1 forbids
incremental side-files precisely so the measurement is honest.  Bulk building
(`index --all`) may batch with `.batch()`.
"""

from __future__ import annotations

import json
from pathlib import Path

from index_base import IndexBackend, Posting, atomic_write_bytes, register, term_sort_key

__all__ = ["JsonIndex"]


class JsonIndex(IndexBackend):
    def __init__(self, workspace, positions: bool = True) -> None:
        super().__init__(workspace, positions)
        self.path = Path(self.workspace) / "datamarts" / "inverted_index.json"
        self._data: dict[str, dict[int, tuple[int, list[int]]]] | None = None

    # ---------------------------------------------------------------- storage

    def _load(self) -> dict[str, dict[int, tuple[int, list[int]]]]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        data: dict[str, dict[int, tuple[int, list[int]]]] = {}
        for term, entry in raw.items():
            by_book: dict[int, tuple[int, list[int]]] = {}
            for posting in entry["postings"]:
                book_id, tf = posting[0], posting[1]
                pos = list(posting[2]) if len(posting) > 2 else []
                by_book[book_id] = (tf, pos)
            data[term] = by_book
        return data

    def _read_state(self) -> dict[str, dict[int, tuple[int, list[int]]]]:
        """State for reads: parsed once and cached."""
        if self._data is None:
            self._data = self._load()
        return self._data

    def _store(self, book_id: int, postings: dict[str, tuple[int, list[int]]]) -> None:
        # Outside a batch every single-book update re-reads the file from disk,
        # because that load-merge-rewrite cost is what experiment E8 measures
        # (§6.1).  Inside a batch the state is read once and kept.
        if not self._batching or self._data is None:
            self._data = self._load()
        data = self._data

        # Replace this book's posting for each term it contains (invariant I4).
        # No global scan for stale terms: a book's text never changes once it
        # is in the datalake, so a re-index cannot drop a term.  All three
        # backends follow this same rule -- see index_base.
        for term, (tf, pos) in postings.items():
            data.setdefault(term, {})[book_id] = (tf, pos)

    def commit(self) -> None:
        data = self._data
        if data is None:
            return
        atomic_write_bytes(self.path, self._serialise(data).encode("utf-8"))

    def _serialise(self, data: dict[str, dict[int, tuple[int, list[int]]]]) -> str:
        out: dict[str, dict] = {}
        for term in sorted(data, key=term_sort_key):
            by_book = data[term]
            postings = []
            for book_id in sorted(by_book):
                tf, pos = by_book[book_id]
                postings.append([book_id, tf, sorted(pos)] if self.positions
                                else [book_id, tf])
            out[term] = {"df": len(postings), "postings": postings}
        # ensure_ascii=False keeps real UTF-8 in the file; the separators keep
        # the bytes free of insignificant whitespace (§7).
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))

    # ---------------------------------------------------------------- reading

    def terms(self) -> list[str]:
        return sorted(self._read_state(), key=term_sort_key)

    def postings_of(self, term: str) -> list[Posting]:
        by_book = self._read_state().get(term)
        if not by_book:
            return []
        return [(book_id, by_book[book_id][0], sorted(by_book[book_id][1]))
                for book_id in sorted(by_book)]


register("json", JsonIndex)
