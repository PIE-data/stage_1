#!/usr/bin/env python3
"""
Generate (or verify) spec/golden/tokens_20.jsonl.  SPEC.md §3.4.

    python tools/make_golden_tokens.py            # write the fixture
    python tools/make_golden_tokens.py --check    # verify it, exit 1 on drift

This file is the oracle for the two ports: a Node or Go tokenizer is finished
when it reproduces these numbers and these hashes exactly.  Once committed it
is FROZEN.  If a later change to the tokenizer or the splitter moves a hash,
that is not a fixture to refresh -- it is a spec change, and it invalidates
every port that already matched the old one.  Hence --check, which is what CI
runs.

Note that the tokenizer is called twice per book: once for the raw stream and
once for the filtered one.  Reimplementing the filter here to save a pass would
put a second copy of SPEC.md §3.3 in the repository, and the two copies would
drift.  Twenty books take a few seconds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "python" / "core"))
sys.path.insert(0, str(REPO / "src" / "python" / "datalake"))

from splitter import split_file  # noqa: E402
from tokenizer import extract_tokens, load_stopwords, tokenize  # noqa: E402

GOLDEN = REPO / "spec" / "golden"
MANIFEST = GOLDEN / "manifest_20.txt"
STOPWORDS = REPO / "spec" / "stopwords_en.txt"
FIXTURE = GOLDEN / "tokens_20.jsonl"


def record(book_id: int, stopwords: frozenset[str]) -> dict:
    _, body = split_file(GOLDEN / f"{book_id}.txt")

    raw = extract_tokens(body)
    kept = [token for token, _position in tokenize(body, stopwords)]

    # SPEC.md §3.4: SHA-256 of the kept tokens joined by "\n", UTF-8 encoded.
    # No trailing newline, no separator other than "\n" -- a trailing newline
    # here would be invisible in Python and cost a day in Go.
    digest = hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()

    return {
        "book_id": book_id,
        "n_tokens_raw": len(raw),
        "n_tokens_kept": len(kept),
        "sha256_tokens": digest,
    }


def build() -> list[dict]:
    if not MANIFEST.exists():
        sys.exit(f"missing {MANIFEST}")
    ids = sorted(
        int(line.strip())
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    stopwords = load_stopwords(STOPWORDS)

    rows = []
    for book_id in ids:
        row = record(book_id, stopwords)
        rows.append(row)
        print(
            f"  {book_id:>6}  raw {row['n_tokens_raw']:>8}  "
            f"kept {row['n_tokens_kept']:>8}  {row['sha256_tokens'][:16]}…",
            flush=True,
        )
    return rows


def serialise(rows: list[dict]) -> str:
    # Sorted by book_id, compact separators, LF endings: the file is an asset
    # the other two languages read, so its bytes are part of the contract.
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify the committed fixture instead of writing it")
    args = ap.parse_args()

    print(f"tokenizing the golden books from {MANIFEST.name} ...")
    text = serialise(build())

    if args.check:
        if not FIXTURE.exists():
            print(f"\n{FIXTURE} does not exist", file=sys.stderr)
            return 1
        current = FIXTURE.read_text(encoding="utf-8")
        if current == text:
            print("\nfixture matches")
            return 0
        print("\nFIXTURE DRIFT -- the tokenizer or the splitter changed.",
              file=sys.stderr)
        print("This is a spec change: it invalidates every port that already "
              "matched the frozen values. Do not refresh it silently.",
              file=sys.stderr)
        for want, got in zip(current.splitlines(), text.splitlines()):
            if want != got:
                print(f"  committed: {want}\n  now      : {got}", file=sys.stderr)
        return 1

    FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    print(f"\nwrote {FIXTURE}  ({len(text.splitlines())} books)")
    print("This fixture is now FROZEN. Ivan and Manuel implement against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
