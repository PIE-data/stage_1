#!/usr/bin/env python3
"""
Freeze (or verify) spec/golden/expected.sha256.  SPEC.md §7, task T23.

    python tools/freeze_expected.py            # build, compare, freeze
    python tools/freeze_expected.py --check    # verify, exit 1 on drift

What it does: indexes the 20 golden books with EVERY backend, exports each one
canonically, and refuses to write anything unless all three hashes are equal.
Three equal hashes in one language is the precondition for the real gate --
nine equal hashes across three languages (T35).

Once written, this file is law for Node and Go.  A change that moves the hash
is a change to the specification, not a fixture to regenerate: it silently
invalidates every port that already matched the old value.  That is why the
frozen file is never overwritten without --force.

The file is written in `sha256sum -c` format so CI can check an export with one
command:

    <hash>  canonical.json
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for sub in ("core", "datalake", "datamart"):
    sys.path.insert(0, str(REPO / "src" / "python" / sub))

from canonical import canonical_bytes  # noqa: E402
from index_base import open_index  # noqa: E402
from splitter import split_file  # noqa: E402
from tokenizer import load_stopwords, tokenize  # noqa: E402

GOLDEN = REPO / "spec" / "golden"
MANIFEST = GOLDEN / "manifest_20.txt"
EXPECTED = GOLDEN / "expected.sha256"
BACKENDS = ("json", "folder", "sqlite")
EXPORT_NAME = "canonical.json"


def load_books() -> list[tuple[int, list[tuple[str, int]]]]:
    stopwords = load_stopwords(REPO / "spec" / "stopwords_en.txt")
    ids = sorted(
        int(line.strip())
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    books = []
    for book_id in ids:
        _, body = split_file(GOLDEN / f"{book_id}.txt")
        books.append((book_id, tokenize(body, stopwords)))
    return books


def hash_for(backend: str, books, workspace: Path) -> str:
    index = open_index(backend, workspace / backend)
    with index.batch() as batched:
        for book_id, tokens in books:
            batched.add_book(book_id, tokens)
    data = canonical_bytes(index)
    index.close()
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an already frozen hash -- see the docstring")
    args = ap.parse_args()

    books = load_books()
    print(f"{len(books)} golden books, {sum(len(t) for _, t in books)} kept tokens\n")

    hashes = {}
    with tempfile.TemporaryDirectory() as tmp:
        for backend in BACKENDS:
            hashes[backend] = hash_for(backend, books, Path(tmp))
            print(f"  {backend:<7} {hashes[backend]}")

    distinct = set(hashes.values())
    if len(distinct) != 1:
        print("\nTHE THREE BACKENDS DISAGREE. Nothing was written.", file=sys.stderr)
        print("Until they agree there is no oracle, and no port can be verified.",
              file=sys.stderr)
        return 1

    digest = distinct.pop()
    line = f"{digest}  {EXPORT_NAME}\n"
    print(f"\nthree backends agree: {digest}")

    if args.check:
        if not EXPECTED.exists():
            print(f"{EXPECTED} does not exist -- not frozen yet", file=sys.stderr)
            return 1
        if EXPECTED.read_text(encoding="utf-8") == line:
            print("matches the frozen value")
            return 0
        print("\nCANONICAL HASH DRIFT.", file=sys.stderr)
        print(f"  frozen: {EXPECTED.read_text(encoding='utf-8').strip()}", file=sys.stderr)
        print(f"  now   : {line.strip()}", file=sys.stderr)
        print("This invalidates every port that matched the frozen value. It is a"
              " spec change, not a fixture refresh.", file=sys.stderr)
        return 1

    if EXPECTED.exists() and EXPECTED.read_text(encoding="utf-8") != line and not args.force:
        print(f"\n{EXPECTED} is already frozen with a different value."
              " Re-run with --force only if the group agreed to it.", file=sys.stderr)
        return 1

    EXPECTED.write_text(line, encoding="utf-8", newline="\n")
    print(f"wrote {EXPECTED}")
    print("\nFROZEN. Node and Go must now reproduce this exact hash with all three"
          " backends: nine cells, one value.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
