#!/usr/bin/env python3
"""
Build spec/queries/{single,and2,and3,absent}.txt.  Issue #9, task T07.

    python tools/make_queries.py                      # uses manifest_1000
    python tools/make_queries.py --manifest spec/corpus/manifest_100.txt
    python tools/make_queries.py --check

These are the query workloads for experiment E7.  Four files, 100 queries each,
one query per line, terms separated by a single space:

    single.txt   one term      -- the cost of a plain lookup
    and2.txt     two terms     -- the cost of intersecting two posting lists
    and3.txt     three terms   -- how that intersection scales
    absent.txt   one term      -- a term that is NOT in the index

The absent set is the one that separates the three backends most sharply:
`json` must still load the whole file, `folder` fails on a single open(),
`sqlite` on one B-tree descent.

**Why this cannot be done by eyeballing the text.** A term is only in the index
if it survives SPEC.md §3.3: at least 2 code points, at most 40, not all digits,
not a stop word.  A "rare" term picked by rough counting may satisfy none of
those and the benchmark would then be timing lookups of something that can
never be found -- measuring the absent case while believing it measures the
rare one.  So the vocabulary here is produced by the real tokenizer, over the
real corpus, and the absent terms are *verified* absent rather than assumed.

Frequency bands come from document frequency (how many books contain the term),
because that is what determines posting-list length, which is what the query
actually costs:

    high   the most common terms in the corpus
    mid    around the median of the vocabulary
    rare   present in only two or three books

single.txt mixes the three bands in equal parts rather than being one band, so
one file exercises short and long posting lists alike.

For and2/and3 the pairs and triples are built from the higher bands downwards:
two high-frequency terms almost always intersect, a high with a rare one almost
never does.  Both cases are wanted -- an AND that returns nothing early-exits
and an AND that returns thousands does not -- so the mix is deliberate and
fixed, not random.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import string
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for sub in ("core", "datalake"):
    sys.path.insert(0, str(REPO / "src" / "python" / sub))

from splitter import split_file  # noqa: E402
from tokenizer import load_stopwords, tokenize  # noqa: E402

MIRROR = REPO / "infra" / "mirror"
QUERIES = REPO / "spec" / "queries"
STOPWORDS_PATH = REPO / "spec" / "stopwords_en.txt"
SEED = 42
COUNT = 100

_STOPWORDS: frozenset[str] | None = None
_MIRROR: Path | None = None


def _init(mirror: str) -> None:
    """Runs once per worker process.

    The mirror path and the stop-word list are passed in rather than read from
    module globals: on Windows a worker is a fresh interpreter that re-imports
    this module, so anything main() assigned to a global would be lost.
    """
    global _STOPWORDS, _MIRROR
    _MIRROR = Path(mirror)
    _STOPWORDS = load_stopwords(STOPWORDS_PATH)


def _worker(book_id: int) -> list[str]:
    """Distinct indexable terms of one book.  Runs in a separate process."""
    _, body = split_file(_MIRROR / f"{book_id}.txt")
    return sorted({term for term, _position in tokenize(body, _STOPWORDS)})


def document_frequencies(ids: list[int], workers: int, mirror: Path) -> Counter:
    df: Counter = Counter()
    done = 0
    with mp.Pool(workers, initializer=_init, initargs=(str(mirror),)) as pool:
        for terms in pool.imap_unordered(_worker, ids, chunksize=4):
            df.update(terms)
            done += 1
            if done % 25 == 0 or done == len(ids):
                print(f"  {done}/{len(ids)} books, {len(df)} distinct terms",
                      flush=True)
    return df


def bands(df: Counter, n_books: int) -> dict[str, list[str]]:
    """Split the vocabulary into high / mid / rare by document frequency.

    Bands are taken from the df-ranked vocabulary, not from equal thirds of it.
    Word frequencies are Zipfian: the top THIRD of a 140 000-term vocabulary
    still consists of terms appearing in five or six books out of a thousand,
    so banding by thirds would produce three sets of equally short posting
    lists and experiment E7 would never exercise a long one -- the single
    measurement it exists to make.

        high   the 500 most widespread terms      (longest posting lists)
        mid    a window a quarter down the ranking
        rare   present in exactly 2 or 3 books    (shortest useful lists)

    Terms in a single book are excluded: an AND query containing one can only
    return that book, which makes the intersection degenerate.

    The actual df range of each band is printed, because it is corpus-dependent
    and belongs in the report next to the E7 numbers.
    """
    usable = sorted(
        ((term, count) for term, count in df.items() if count >= 2),
        key=lambda item: (-item[1], item[0]),
    )
    if len(usable) < 20 * COUNT:
        sys.exit(f"vocabulary too small: {len(usable)} terms with df >= 2")

    quarter = len(usable) // 4
    out = {
        "high": [t for t, _ in usable[:500]],
        "mid": [t for t, _ in usable[quarter : quarter + 2000]],
        "rare": [t for t, c in usable if c in (2, 3)],
    }

    for name, terms in out.items():
        if len(terms) < COUNT:
            sys.exit(f"band {name!r} has only {len(terms)} terms, {COUNT} needed")
        lo = min(df[t] for t in terms)
        hi = max(df[t] for t in terms)
        share = f"{100 * lo / n_books:.1f}%..{100 * hi / n_books:.1f}%" if n_books else ""
        print(f"  band {name:<5} {len(terms):>7} terms, df {lo}..{hi}  {share} of books")
    return out


def make_absent(vocabulary: set[str], stopwords: frozenset[str], rng: random.Random) -> list[str]:
    """Invent terms that are legal tokens but appear nowhere in the corpus."""
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < COUNT:
        word = "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(7, 11)))
        if word in seen or word in vocabulary:
            continue
        seen.add(word)
        # It must survive the real filters, otherwise it is not a term the
        # index could ever hold and the query tests the wrong thing.
        if tokenize(word, stopwords) != [(word, 0)]:
            continue
        out.append(word)
    return out


def build_workloads(df: Counter, stopwords: frozenset[str], n_books: int) -> dict[str, list[str]]:
    rng = random.Random(SEED)
    band = bands(df, n_books)
    vocabulary = set(df)

    # One shuffled pool per band, drawn from without replacement: no term
    # repeats across the workloads, and no line can contain the same term
    # twice.  "merely AND merely" is not an intersection, it is a lookup with
    # extra steps, and it would quietly flatten the AND-2 measurement.
    pools = {name: rng.sample(terms, len(terms)) for name, terms in band.items()}
    cursor = {name: 0 for name in pools}

    def take(name: str) -> str:
        term = pools[name][cursor[name]]
        cursor[name] += 1
        return term

    def line(*names: str) -> str:
        terms: list[str] = []
        for name in names:
            term = take(name)
            while term in terms:
                term = take(name)
            terms.append(term)
        return " ".join(terms)

    single = [take("high") for _ in range(34)]
    single += [take("mid") for _ in range(33)]
    single += [take("rare") for _ in range(33)]

    # The mix is fixed, not random: 50 intersections that return a lot, 30 that
    # return some, 20 that almost always return nothing.
    and2 = ([line("high", "high") for _ in range(50)]
            + [line("high", "mid") for _ in range(30)]
            + [line("mid", "rare") for _ in range(20)])

    and3 = ([line("high", "high", "high") for _ in range(50)]
            + [line("high", "mid", "mid") for _ in range(30)]
            + [line("mid", "mid", "rare") for _ in range(20)])

    return {
        "single": single,
        "and2": and2,
        "and3": and3,
        "absent": make_absent(vocabulary, stopwords, rng),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(REPO / "spec" / "corpus" / "manifest_1000.txt"))
    ap.add_argument("--mirror", default=str(MIRROR))
    ap.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-cache", action="store_true",
                    help="recompute document frequencies instead of reusing the cache")
    args = ap.parse_args()

    mirror = Path(args.mirror)

    manifest = Path(args.manifest)
    if not manifest.exists():
        sys.exit(f"missing {manifest} -- run tools/make_manifests.py first")
    ids = sorted(int(line) for line in manifest.read_text(encoding="utf-8").split())

    # The document-frequency pass is the expensive part.  Cache it under
    # workspace/ (gitignored) so that re-running to adjust the bands costs
    # seconds instead of re-tokenizing the corpus.
    cache = REPO / "workspace" / f"df_{manifest.stem}.json"
    if cache.exists() and not args.no_cache:
        print(f"reusing {cache.relative_to(REPO)} (--no-cache to recompute)")
        df = Counter(json.loads(cache.read_text(encoding="utf-8")))
    else:
        print(f"tokenizing {len(ids)} books from {manifest.name} on {args.workers} processes")
        print("(this is the real tokenizer over the real corpus -- expect minutes)")
        df = document_frequencies(ids, args.workers, mirror)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(df), encoding="utf-8")

    stopwords = load_stopwords(STOPWORDS_PATH)
    workloads = build_workloads(df, stopwords, len(ids))

    print(f"\nvocabulary: {len(df)} terms, {sum(1 for c in df.values() if c >= 2)} with df >= 2")
    for name, lines in workloads.items():
        assert len(lines) == COUNT, f"{name}: {len(lines)} lines, expected {COUNT}"
        print(f"  {name:<7} {len(lines)} queries   e.g. {lines[0]!r}")

    QUERIES.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name, lines in workloads.items():
        path = QUERIES / f"{name}.txt"
        text = "".join(line + "\n" for line in lines)
        if args.check:
            state = "ok" if path.exists() and path.read_text(encoding="utf-8") == text else "DIFFERS"
            failures += state != "ok"
            print(f"  {name}: {state}")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")

    if args.check:
        return 1 if failures else 0

    print(f"\nwrote {len(workloads)} files to {QUERIES}. Frozen from here on: the"
          " benchmark numbers are only comparable if every run uses these exact"
          " queries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
