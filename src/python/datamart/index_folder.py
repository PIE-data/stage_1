"""
`folder` backend — one file per term.  SPEC.md §6.2.

    datamarts/inverted_index/<BUCKET>/<safe_term>.txt

    BUCKET     the term's first code point uppercased when it is an ASCII
               letter, otherwise "_"
    safe_term  the term with every code point outside [a-z0-9] percent-encoded
               as %XX per UTF-8 byte
    content    one posting per line: "<book_id>\\t<tf>\\t<pos>,<pos>,…\\n",
               ascending by book_id; the third column is omitted when the index
               was built without positions

Why the encoding is not optional: raw terms as filenames collide on
case-insensitive filesystems ("Apple" and "apple" become one file on NTFS and
APFS), break on names Windows reserves (CON, PRN, AUX, NUL, COM1…), and break
on any term containing a separator.  Every one of those failures is silent --
the index simply loses postings.  Percent-encoding every non [a-z0-9] code
point removes the whole class of problems at once, and must be identical in
Node and in Go or the three indexes diverge.

Note the bucket rule is written against ASCII letters, not str.upper(): "ß"
uppercases to "SS" (two code points) in Python and in JavaScript but not in Go,
and a Turkish locale maps "i" to "İ".  Terms reach here already lowercased and
ASCII-folded by the tokenizer, so this only ever matters for non-Latin scripts,
which land in "_" by design.
"""

from __future__ import annotations

from pathlib import Path

from index_base import IndexBackend, Posting, atomic_write_bytes, register, term_sort_key

__all__ = ["FolderIndex", "encode_term", "decode_term", "bucket_of"]

_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def encode_term(term: str) -> str:
    """Percent-encode every code point outside [a-z0-9] (§6.2)."""
    out: list[str] = []
    for ch in term:
        if ch in _UNRESERVED:
            out.append(ch)
        else:
            out.extend(f"%{byte:02X}" for byte in ch.encode("utf-8"))
    return "".join(out)


def decode_term(safe: str) -> str:
    """Inverse of encode_term.  Decoding happens on BYTES, then UTF-8."""
    raw = bytearray()
    i = 0
    while i < len(safe):
        if safe[i] == "%":
            raw.append(int(safe[i + 1 : i + 3], 16))
            i += 3
        else:
            raw.extend(safe[i].encode("utf-8"))
            i += 1
    return raw.decode("utf-8")


def bucket_of(term: str) -> str:
    """Uppercase first code point when it is an ASCII letter, else "_"."""
    first = term[:1]
    return first.upper() if "a" <= first <= "z" or "A" <= first <= "Z" else "_"


class FolderIndex(IndexBackend):
    def __init__(self, workspace, positions: bool = True) -> None:
        super().__init__(workspace, positions)
        self.root = Path(self.workspace) / "datamarts" / "inverted_index"
        # Pending merges, flushed on commit.  One file per term is written at
        # most once per commit: inside a batch that is one write per term, not
        # one per book.
        self._pending: dict[str, dict[int, tuple[int, list[int]]]] = {}

    # ---------------------------------------------------------------- paths

    def path_of(self, term: str) -> Path:
        return self.root / bucket_of(term) / f"{encode_term(term)}.txt"

    # ---------------------------------------------------------------- storage

    def _read_file(self, term: str) -> dict[int, tuple[int, list[int]]]:
        path = self.path_of(term)
        if not path.exists():
            return {}
        by_book: dict[int, tuple[int, list[int]]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            parts = line.split("\t")
            book_id, tf = int(parts[0]), int(parts[1])
            pos = [int(p) for p in parts[2].split(",")] if len(parts) > 2 and parts[2] else []
            by_book[book_id] = (tf, pos)
        return by_book

    def _render(self, by_book: dict[int, tuple[int, list[int]]]) -> bytes:
        lines = []
        for book_id in sorted(by_book):
            tf, pos = by_book[book_id]
            if self.positions:
                lines.append(f"{book_id}\t{tf}\t{','.join(str(p) for p in sorted(pos))}")
            else:
                lines.append(f"{book_id}\t{tf}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _store(self, book_id: int, postings: dict[str, tuple[int, list[int]]]) -> None:
        for term, entry in postings.items():
            self._pending.setdefault(term, {})[book_id] = entry

    def commit(self) -> None:
        for term, new_postings in self._pending.items():
            merged = self._read_file(term)
            merged.update(new_postings)
            data = self._render(merged)
            path = self.path_of(term)
            # Invariant I4: re-running on a complete corpus performs zero
            # writes.  Comparing first is cheaper than the write it avoids.
            if path.exists() and path.read_bytes() == data:
                continue
            atomic_write_bytes(path, data)
        self._pending.clear()

    # ---------------------------------------------------------------- reading

    def terms(self) -> list[str]:
        if not self.root.exists():
            return []
        found = [
            decode_term(path.stem)
            for path in self.root.glob("*/*.txt")
        ]
        return sorted(found, key=term_sort_key)

    def postings_of(self, term: str) -> list[Posting]:
        by_book = self._read_file(term)
        if term in self._pending:
            by_book.update(self._pending[term])
        if not by_book:
            return []
        return [(book_id, by_book[book_id][0], sorted(by_book[book_id][1]))
                for book_id in sorted(by_book)]


register("folder", FolderIndex)
