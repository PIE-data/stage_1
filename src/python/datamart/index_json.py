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

Memory.  Each posting is held as its own serialised JSON text, e.g.
"[12,3,[4,17,90]]", not as a Python list of int objects: an int object costs
~28 bytes plus an 8-byte list slot, against one byte per digit here.  The file
is also read and written one term at a time, never as one dict plus one giant
string.  None of this changes a byte of the file -- the canonical-export hash
(§7) is the check -- nor the load-merge-rewrite work §6.1 prescribes; it only
stops Python's object overhead from dominating the measurement (E6/E8/E9).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from index_base import IndexBackend, Posting, register, term_sort_key

__all__ = ["JsonIndex"]

# term -> {book_id -> the posting's serialised JSON text}
State = dict[str, dict[int, str]]

_decoder = json.JSONDecoder()


def _posting_text(book_id: int, tf: int, positions: list[int] | None) -> str:
    """One posting exactly as §7 writes it: no spaces, positions ascending."""
    if positions is None:
        return f"[{book_id},{tf}]"
    return f"[{book_id},{tf},[{','.join(map(str, sorted(positions)))}]]"


def _parse_posting(text: str) -> Posting:
    value = json.loads(text)
    return (value[0], value[1], list(value[2]) if len(value) > 2 else [])


class JsonIndex(IndexBackend):
    def __init__(self, workspace, positions: bool = True) -> None:
        super().__init__(workspace, positions)
        self.path = Path(self.workspace) / "datamarts" / "inverted_index.json"
        self._data: State | None = None

    # ---------------------------------------------------------------- storage

    def _load(self) -> State:
        """Parse the file one term at a time.

        json.loads() on the whole file would build every posting as Python
        lists first -- several times the file size -- only to convert them.
        raw_decode() parses one term's entry, which is turned into posting
        texts and dropped before the next one is read.
        """
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        data: State = {}
        decode = _decoder.raw_decode
        n = len(text)
        i = text.index("{") + 1
        while True:
            while i < n and text[i] in " \t\r\n,":
                i += 1
            if i >= n or text[i] == "}":
                break
            term, i = decode(text, i)
            i = text.index(":", i) + 1
            entry, i = decode(text, i)
            data[term] = {
                p[0]: _posting_text(p[0], p[1], p[2] if len(p) > 2 else None)
                for p in entry["postings"]
            }
        return data

    def _read_state(self) -> State:
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
            data.setdefault(term, {})[book_id] = _posting_text(
                book_id, tf, pos if self.positions else None)

    def commit(self) -> None:
        data = self._data
        if data is None:
            return
        self._write(data)

    def _write(self, data: State) -> None:
        """Same bytes as json.dumps(..., ensure_ascii=False, separators=(",", ":"))
        of {"<term>": {"df": n, "postings": [...]}}, written in chunks.

        Terms are only letters, digits and apostrophes (§3), so json.dumps of
        the key alone is its exact encoding.  Atomicity as atomic_write_bytes:
        .part -> fsync -> rename -> fsync parent (§2.4).
        """
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("{")
            first = True
            for term in sorted(data, key=term_sort_key):
                by_book = data[term]
                if not first:
                    fh.write(",")
                first = False
                fh.write(json.dumps(term, ensure_ascii=False))
                fh.write(f':{{"df":{len(by_book)},"postings":[')
                fh.write(",".join(by_book[b] for b in sorted(by_book)))
                fh.write("]}")
            fh.write("}")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
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

    # ---------------------------------------------------------------- reading

    def terms(self) -> list[str]:
        return sorted(self._read_state(), key=term_sort_key)

    def postings_of(self, term: str) -> list[Posting]:
        by_book = self._read_state().get(term)
        if not by_book:
            return []
        return [_parse_posting(by_book[b]) for b in sorted(by_book)]


register("json", JsonIndex)
