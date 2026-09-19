#!/usr/bin/env python3
"""
Build the local Project Gutenberg mirror.  (issue #8)

Polite, resumable, validating.  Run it and leave it: at 1 req/s, 2000 accepted
books take roughly 1.5-2 hours including rejects.

    python tools/build_mirror.py --target 2000
    python tools/build_mirror.py --target 2000        # re-run: resumes, skips what it has

Output
    infra/mirror/<id>.txt          raw book, exactly as served
    infra/mirror/_accepted.txt     one id per line, ascending  -> input for the manifests (#9)
    infra/mirror/_rejected.txt     <id>\t<reason>              -> audit trail, and it stops retries

Why a mirror at all: benchmarking against live Gutenberg measures their rate
limiter and their bandwidth, not our code, and the numbers would not be
reproducible between one day and the next.  It also avoids getting the group's
IPs banned mid-project -- gutenberg.org disallows crawlers in robots.txt and
does block aggressive downloading.
"""

from __future__ import annotations

import argparse
import http.client
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://www.gutenberg.org"
UA = "PIE-data-Stage1/1.0 (+https://github.com/PIE-data/stage_1) academic coursework"

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

# Gutenberg ids run to roughly 70000, with many gaps.
ID_MAX = 70000

LANG_RE = re.compile(r"^Language:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def fetch(book_id: int, timeout: int = 60) -> tuple[str, str | None]:
    """
    Return (status, text).

    status is one of:
      "ok"      -> text is the book
      "gone"    -> 404; a permanent gap in the id space, safe to record forever
      "error"   -> transient: network down, proxy, timeout, 5xx

    The distinction matters.  A transient failure must NOT be written to
    _rejected.txt: if the network is down or a proxy is blocking, every id
    would be permanently marked dead and a later re-run would skip the whole
    corpus, silently.
    """
    url = f"{BASE}/cache/epub/{book_id}/pg{book_id}.txt"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    delay = 1.0
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            # Gutenberg occasionally serves Latin-1 mislabelled as UTF-8.
            return "ok", raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "gone", None          # gap in the id space -- never retry
            if e.code in (403, 429):
                # Rate limited or blocked: the signal that we are being impolite.
                print(f"  ! HTTP {e.code} on {book_id} -- backing off 60s", flush=True)
                time.sleep(60)
                continue
        except (
            urllib.error.URLError,
            http.client.HTTPException,   # IncompleteRead, BadStatusLine, ... -- NOT an OSError
            TimeoutError,
            OSError,
        ):
            pass
        if attempt < 2:
            time.sleep(delay)
            delay *= 2
    return "error", None


def validate(text: str) -> str | None:
    """Return a rejection reason, or None when the book is usable."""
    if START_MARKER not in text:
        return "NO_START_MARKER"
    if END_MARKER not in text:
        return "NO_END_MARKER"

    header = text.split(START_MARKER, 1)[0]
    m = LANG_RE.search(header)
    if not m:
        return "NO_LANGUAGE"
    if m.group(1).strip().lower() != "english":
        return f"LANG_{m.group(1).strip()[:20].replace(chr(9), ' ')}"

    body = text.split(START_MARKER, 1)[1].rsplit(END_MARKER, 1)[0]
    if len(body) < 10_000:
        return "TOO_SHORT"          # stubs and index pages, not real books
    return None


def load_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        tok = line.split("\t", 1)[0].strip()
        if tok.isdigit():
            out.add(int(tok))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2000, help="accepted books wanted")
    ap.add_argument("--out", default="infra/mirror", help="mirror directory")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--seed", type=int, default=42, help="fixed, so the id order is reproducible")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    acc_path, rej_path = out / "_accepted.txt", out / "_rejected.txt"

    accepted = load_ids(acc_path)
    rejected = load_ids(rej_path)
    seen = accepted | rejected

    print(f"mirror   : {out.resolve()}")
    print(f"resuming : {len(accepted)} accepted, {len(rejected)} rejected")
    print(f"target   : {args.target}\n")

    if len(accepted) >= args.target:
        print("Target already reached. Nothing to do.")
        return 0

    # Fixed seed -> the same candidate order on every machine and every re-run,
    # so the mirror is reproducible rather than whatever we happened to grab.
    rng = random.Random(args.seed)
    candidates = list(range(1, ID_MAX + 1))
    rng.shuffle(candidates)

    t0 = time.time()
    tried = 0
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 15

    # Progress must be measured against THIS session's work, not the resumed
    # total: dividing the resumed count by this session's attempts produced
    # nonsense like "accept 4796%".
    start_accepted = len(accepted)

    try:
        for book_id in candidates:
            if len(accepted) >= args.target:
                break
            if book_id in seen:
                continue

            tried += 1
            status, text = fetch(book_id)

            if status == "error":
                # Transient. Record nothing -- this id must stay retryable.
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(
                        f"\n{MAX_CONSECUTIVE_ERRORS} consecutive network failures. "
                        "Stopping rather than marking the whole corpus dead.\n"
                        "Check your connection / proxy, then re-run to resume.",
                        file=sys.stderr,
                    )
                    break
                time.sleep(args.delay)
                continue

            consecutive_errors = 0

            if status == "gone":
                rejected.add(book_id)
                with rej_path.open("a", encoding="utf-8") as f:
                    f.write(f"{book_id}\tNOT_FOUND\n")
            else:
                reason = validate(text)
                if reason:
                    rejected.add(book_id)
                    with rej_path.open("a", encoding="utf-8") as f:
                        f.write(f"{book_id}\t{reason}\n")
                else:
                    (out / f"{book_id}.txt").write_text(text, encoding="utf-8")
                    accepted.add(book_id)
                    with acc_path.open("a", encoding="utf-8") as f:
                        f.write(f"{book_id}\n")

            if tried % 25 == 0:
                el = time.time() - t0
                gained = len(accepted) - start_accepted        # this session only
                pct = 100 * gained / tried
                rate = gained / el * 3600 if el > 0 else 0     # accepted per hour
                remaining = args.target - len(accepted)
                eta_min = remaining / rate * 60 if rate > 0 else float("inf")
                print(
                    f"  {len(accepted):6d}/{args.target}  "
                    f"tried {tried:5d}  accept {pct:5.1f}%  "
                    f"{rate:6.0f}/h  eta {eta_min:6.1f} min",
                    flush=True,
                )

            time.sleep(args.delay)

    except KeyboardInterrupt:
        print("\nInterrupted. State is on disk -- re-run to resume.")

    # Keep the accepted list sorted and unique, so the manifests in #9 are
    # deterministic regardless of the order books happened to arrive in.
    acc_path.write_text("\n".join(str(i) for i in sorted(accepted)) + "\n", encoding="utf-8")

    print(f"\naccepted : {len(accepted)}")
    print(f"rejected : {len(rejected)}")
    print(f"elapsed  : {(time.time() - t0)/60:.1f} min")
    print(f"\nNext: issue #9 builds the manifests from {acc_path}")
    return 0 if len(accepted) >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
