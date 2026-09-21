#!/usr/bin/env python3
"""
Find awkward books in the mirror, to build the golden fixture.  (issue #9)

    python tools/pick_golden.py                     # report only
    python tools/pick_golden.py --suggest           # report + a proposed set of 20
    python tools/pick_golden.py --synthetic-from 8527   # build the missing-case fixture

The golden corpus is chosen to be AWKWARD, not representative.  Twenty ordinary
English novels would make the conformance test pass without protecting against
anything.  What protects you is the book with no Author line, the one whose
title wraps onto a second line, the one whose text carries characters outside
the Basic Multilingual Plane -- the cases where a plausible-but-wrong
implementation diverges.

v2 notes (after the first run over the 10 000-book mirror):
  * Gutenberg's CURRENT header format is not the one the old rules assumed.
    `Release date:` always carries a trailing `[eBook #<id>]`, authors usually
    have no life-span suffix, and a wrapped field is separated from its own
    continuation by a BLANK line.  The v1 categories for date format and
    life-span therefore matched ~100% of the corpus and were pure noise; they
    are replaced by checks that describe the real format.
  * A continuation line is now only counted when it does NOT itself look like
    `Field: value`, so `Most recently updated:` (which every book has) no
    longer drowns out genuinely wrapped titles.
  * Two new categories that matter for the conformance hash: U+FFFD (broken
    source encoding) and 4-byte UTF-8 sequences (the characters that a Node
    implementation indexing with s[i] will split in half).

Performance note: everything runs on BYTES, not decoded text.  Scanning 10 000
books means ~4 GB; decoding each one and walking it code point by code point
through unicodedata would take hours.  Byte-level scanning stays in C and takes
a few minutes.  Only the header -- a few hundred bytes -- is decoded.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

START_MARKER = b"*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = b"*** END OF THE PROJECT GUTENBERG EBOOK"

FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?):\s*(.*)$")
RELEASE_RE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4} \[eBook #\d+\]$")

REPLACEMENT_CHAR = "�".encode()  # b"\xef\xbf\xbd"

# Books larger than this are excluded from --suggest: the golden fixture runs
# on every push and must stay under about two minutes.
MAX_SUGGEST_BYTES = 2 * 1024 * 1024


def analyse(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    n_start = raw.count(START_MARKER)
    n_end = raw.count(END_MARKER)
    if n_start == 0 or n_end == 0:
        return None

    header_bytes = raw.split(START_MARKER, 1)[0]
    body_bytes = raw.split(START_MARKER, 1)[1].rsplit(END_MARKER, 1)[0]

    header = header_bytes.decode("utf-8", errors="replace")

    fields: dict[str, str] = {}
    wrapped: set[str] = set()
    last: str | None = None
    for line in header.splitlines():
        if not line.strip():
            continue  # a blank line does NOT end a field in the current format
        m = FIELD_RE.match(line)
        if m:
            last = m.group(1).strip().lower()
            fields[last] = m.group(2).strip()
        elif last:
            # Indented, and not "Field: value" -> a genuine wrapped value.
            wrapped.add(last)
            fields[last] += " " + line.strip()

    non_ascii = len(body_bytes) - len(body_bytes.translate(None, delete=bytes(range(128, 256))))
    astral = sum(body_bytes.count(bytes([b])) for b in range(0xF0, 0xF5))

    release = fields.get("release date", "")

    return {
        "id": int(path.stem),
        "title": fields.get("title", ""),
        "author": fields.get("author"),
        "release": release,
        "body_bytes": len(body_bytes),
        "n_start": n_start,
        "n_end": n_end,
        "non_ascii": non_ascii,
        "astral": astral,
        "replacement": body_bytes.count(REPLACEMENT_CHAR),
        "apostrophes": body_bytes.count(b"'") + body_bytes.count("’".encode()),
        "wrapped_fields": sorted(wrapped),
        "non_ascii_title": any(ord(c) > 0x7F for c in fields.get("title", "")),
        "odd_release": bool(release) and not RELEASE_RE.match(release),
        "no_release": not release,
    }


def build_categories(books: list[dict], top: int) -> dict[str, list]:
    cats: dict[str, list] = {}

    # Splitter traps.  On the current corpus these are expected to be EMPTY --
    # that is a finding, not a failure: build the case by hand instead
    # (--synthetic-from).
    cats["END marker quoted in the text (n_end > 1)"] = [b for b in books if b["n_end"] > 1]
    cats["START marker repeated (n_start > 1)"] = [b for b in books if b["n_start"] > 1]

    # Header parser traps.
    cats["no Author field"] = [b for b in books if not b["author"]]
    cats["wrapped header field (real continuation)"] = [b for b in books if b["wrapped_fields"]]
    cats["no Release date field"] = [b for b in books if b["no_release"]]
    cats["release date not 'Month D, YYYY [eBook #N]'"] = [b for b in books if b["odd_release"]]
    cats["non-ASCII characters in the title"] = [b for b in books if b["non_ascii_title"]]

    # Tokenizer / encoding traps.
    cats["4-byte UTF-8 (astral) characters in the body"] = sorted(
        [b for b in books if b["astral"]], key=lambda b: -b["astral"]
    )
    cats["U+FFFD in the body (damaged source encoding)"] = sorted(
        [b for b in books if b["replacement"]], key=lambda b: -b["replacement"]
    )
    cats["heaviest accented text"] = sorted(books, key=lambda b: -b["non_ascii"])[:top]
    cats["most apostrophes"] = sorted(books, key=lambda b: -b["apostrophes"])[:top]

    # Size extremes.
    cats["shortest bodies"] = sorted(books, key=lambda b: b["body_bytes"])[:top]
    cats["longest bodies"] = sorted(books, key=lambda b: -b["body_bytes"])[:top]

    return cats


def describe(b: dict) -> str:
    extra = ""
    if b["wrapped_fields"]:
        extra += f"  [wrapped: {','.join(b['wrapped_fields'])}]"
    if b["n_end"] > 1:
        extra += f"  [n_end={b['n_end']}]"
    if b["astral"]:
        extra += f"  [astral={b['astral']}]"
    if b["replacement"]:
        extra += f"  [U+FFFD={b['replacement']}]"
    return (
        f"   {b['id']:>6}  {b['body_bytes']//1024:>6} KB  "
        f"{(b['title'] or '?')[:40]:<40}  "
        f"{(b['author'] or '-- no author --')[:26]}{extra}"
    )


def suggest(cats: dict[str, list], want: int) -> list[int]:
    """Round-robin over the categories, smallest book first, until `want` ids.

    Round-robin rather than category-by-category: a book that satisfies three
    categories should be spent once, and every category deserves at least one
    representative before any category gets a second.
    """
    pools = {
        name: sorted(
            (b for b in items if b["body_bytes"] <= MAX_SUGGEST_BYTES),
            key=lambda b: b["body_bytes"],
        )
        for name, items in cats.items()
        if items
    }
    chosen: list[int] = []
    seen: set[int] = set()
    while len(chosen) < want and any(pools.values()):
        for name in list(pools):
            while pools[name] and pools[name][0]["id"] in seen:
                pools[name].pop(0)
            if not pools[name]:
                continue
            b = pools[name].pop(0)
            seen.add(b["id"])
            chosen.append(b["id"])
            if len(chosen) >= want:
                break
    return chosen


def make_synthetic(mirror: Path, src_id: int, out: Path) -> None:
    """Build the case the corpus does not contain: the end marker quoted inside
    the body.  A correct splitter takes the LAST occurrence; a plausible wrong
    one takes the first and silently truncates the book."""
    raw = (mirror / f"{src_id}.txt").read_bytes()
    head, rest = raw.split(START_MARKER, 1)
    body, tail = rest.rsplit(END_MARKER, 1)
    injected = (
        b"\r\n\r\nThe librarian read aloud the line printed on the last page:\r\n"
        + END_MARKER
        + b" QUOTED INSIDE THE TEXT ***\r\nand then closed the volume.\r\n\r\n"
    )
    mid = len(body) // 2
    out.write_bytes(head + START_MARKER + body[:mid] + injected + body[mid:] + END_MARKER + tail)
    print(f"wrote {out}  (source {src_id}, end marker injected at byte {mid} of the body)")
    print("Expected behaviour: the split body ends with the ORIGINAL final line,")
    print("not with 'and then closed the volume.'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default="infra/mirror")
    ap.add_argument("--top", type=int, default=10, help="candidates shown per category")
    ap.add_argument("--out", default="", help="also write the report to this file")
    ap.add_argument("--suggest", action="store_true", help="propose a set of 20 ids")
    ap.add_argument("--want", type=int, default=20, help="how many ids to propose")
    ap.add_argument("--synthetic-from", type=int, default=0, metavar="ID",
                    help="write spec/golden/synthetic_quoted_end.txt from this book and exit")
    args = ap.parse_args()

    mirror = Path(args.mirror)

    if args.synthetic_from:
        out = Path("spec/golden/synthetic_quoted_end.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        make_synthetic(mirror, args.synthetic_from, out)
        return 0

    files = [f for f in sorted(mirror.glob("*.txt")) if f.stem.isdigit()]
    if not files:
        print(f"No books in {mirror}", file=sys.stderr)
        return 1

    print(f"scanning {len(files)} books ...", flush=True)
    t0 = time.time()
    books = []
    for i, f in enumerate(files, 1):
        b = analyse(f)
        if b:
            books.append(b)
        if i % 1000 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(files)}  ({el:.0f}s, eta {el/i*(len(files)-i):.0f}s)", flush=True)

    print(f"done in {time.time()-t0:.0f}s, {len(books)} usable\n", flush=True)

    cats = build_categories(books, args.top)

    lines = []
    for name, items in cats.items():
        lines.append(f"## {name}  ({len(items)})")
        if not items:
            lines.append("   none found -- build this case by hand\n")
            continue
        for b in items[: args.top]:
            lines.append(describe(b))
        lines.append("")

    if args.suggest:
        ids = suggest(cats, args.want)
        by_id = {b["id"]: b for b in books}
        total = sum(by_id[i]["body_bytes"] for i in ids)
        lines.append(f"## SUGGESTED SET ({len(ids)} books, {total//1024} KB total)")
        for i in ids:
            lines.append(describe(by_id[i]))
        lines.append("")
        lines.append("  for id in " + " ".join(str(i) for i in ids) + "; do")
        lines.append('    cp "infra/mirror/$id.txt" spec/golden/')
        lines.append("  done")
        lines.append("  printf '%s\\n' " + " ".join(str(i) for i in ids) + " > spec/golden/manifest_20.txt")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"written to {args.out}")

    print("=" * 78)
    print("The suggestion is a starting point, not a decision: read the report and")
    print("swap anything that does not earn its place.  Then add the synthetic case:")
    print("  python tools/pick_golden.py --synthetic-from <a short book id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
