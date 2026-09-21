"""
Canonical export — the equivalence oracle.  SPEC.md §7.

Every implementation, in every language, with every index backend, must emit
THE SAME BYTES for the same corpus.  The SHA-256 of those bytes is the single
number that proves the three implementations are comparable; without it, the
benchmark chapter of the report compares three programs that were never shown
to do the same thing.

Format (§7), and every clause of it is load-bearing:

    JSON, UTF-8, LF, no trailing newline, no insignificant whitespace
    top level   object, keys = terms, sorted by UTF-8 byte order
    value       {"df":<int>,"postings":[[<id>,<tf>,[<pos>,…]],…]}
    postings    ascending by book_id;  positions ascending
    no positions -> the triple becomes a pair [<id>,<tf>]
    integers    no leading zeros, no exponent form

A note for the Node and Go ports, because this is where byte-identical output
usually dies:

  * Go's encoding/json escapes <, > and & as \\u003c, \\u003e and \\u0026 unless
    the encoder has SetEscapeHTML(false).  Python and JavaScript do not escape
    them.
  * JavaScript's JSON.stringify writes astral characters as surrogate pairs in
    UTF-16 memory but encodes them correctly as UTF-8 on output; sorting them,
    however, is wrong with the native `<` operator (see index_base).

Neither bites us today: after §3 the only code points a term can contain are
letters, decimal digits, U+0027 and U+2019 — never <, > or &.  That is a
property of the tokenizer, not a coincidence, so anyone relaxing the token
filter has to come back and re-check this file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["canonical_bytes", "export_canonical", "canonical_sha256"]

# Deliberately no import of the datamart package: this module only needs an
# object exposing terms(), postings_of() and .positions, so it stays in core/
# where SPEC.md and docs/TASKS.md put it, and the ports can mirror the layout.


def _term_sort_key(term: str) -> bytes:
    """UTF-8 byte order (§7).  Same rule as index_base.term_sort_key."""
    return term.encode("utf-8")


def canonical_bytes(index) -> bytes:
    """Serialise the whole index into its canonical byte representation."""
    out: dict[str, dict] = {}
    for term in sorted(index.terms(), key=_term_sort_key):
        postings = index.postings_of(term)
        rows = []
        for book_id, tf, positions in postings:
            if index.positions:
                rows.append([book_id, tf, list(positions)])
            else:
                rows.append([book_id, tf])
        out[term] = {"df": len(rows), "postings": rows}

    # sort_keys is NOT used: Python would sort by code point, which happens to
    # agree here, but spelling out the byte-order key above keeps this file and
    # the two ports describing the same rule.
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


def export_canonical(index, out_path: str | Path) -> bytes:
    """Write the canonical export to `out_path` and return its bytes."""
    data = canonical_bytes(index)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline handling is bypassed entirely: binary mode, no trailing newline.
    path.write_bytes(data)
    return data


def canonical_sha256(index) -> str:
    return hashlib.sha256(canonical_bytes(index)).hexdigest()
