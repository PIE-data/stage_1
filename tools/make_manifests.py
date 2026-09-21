#!/usr/bin/env python3
"""
Build spec/corpus/manifest_{100,1000,10000}.txt.  Issue #9, task T06.

    python tools/make_manifests.py
    python tools/make_manifests.py --check      # verify, write nothing

A manifest fixes WHICH books make up a corpus tier.  Every benchmark in every
language reads the same manifest, so the numbers compare like with like; a
program that picks book ids at runtime makes the whole of report §5 void.

Two properties, both deliberate:

  * **Nested.** manifest_100 is a subset of manifest_1000, which is a subset of
    manifest_10000.  The scalability experiments (E10, E11) plot cost against
    corpus size, and that curve only means something if the bigger corpus is
    the smaller one plus more books, not a different corpus.
  * **Deterministic.** The sample is drawn with a fixed seed from the sorted
    list of mirrored ids, so anyone re-running this gets the same manifests.
    Re-downloading the mirror with the same seed therefore reproduces the
    corpus (SPEC.md §1, --seed).

Once committed, manifests are immutable.  Changing one silently invalidates
every benchmark already recorded against it.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "spec" / "corpus"
SEED = 42


def mirrored_ids(mirror: Path) -> list[int]:
    ids = sorted(int(p.stem) for p in mirror.glob("*.txt") if p.stem.isdigit())
    if not ids:
        sys.exit(f"no books in {mirror}")
    return ids


def build(ids: list[int], tiers: list[int]) -> dict[int, list[int]]:
    """Nested samples, largest first, each drawn from the one above it."""
    tiers = sorted(tiers, reverse=True)
    rng = random.Random(SEED)

    pool = ids
    out: dict[int, list[int]] = {}
    for size in tiers:
        if size > len(pool):
            sys.exit(
                f"tier {size} needs {size} books but only {len(pool)} are available"
            )
        chosen = sorted(rng.sample(pool, size))
        out[size] = chosen
        pool = chosen          # the next, smaller tier is drawn from this one
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default=str(REPO / "infra" / "mirror"))
    ap.add_argument("--tiers", default="100,1000,10000")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    mirror = Path(args.mirror)
    tiers = [int(t) for t in args.tiers.split(",")]

    ids = mirrored_ids(mirror)
    print(f"{len(ids)} books in {mirror}")

    manifests = build(ids, tiers)

    # Validation: every id must exist in the mirror, and the tiers must nest.
    for size, chosen in manifests.items():
        missing = [i for i in chosen if not (mirror / f"{i}.txt").exists()]
        if missing:
            sys.exit(f"manifest_{size}: {len(missing)} ids missing from the mirror")
    ordered = sorted(manifests, reverse=True)
    for bigger, smaller in zip(ordered, ordered[1:]):
        if not set(manifests[smaller]) <= set(manifests[bigger]):
            sys.exit(f"manifest_{smaller} is not a subset of manifest_{bigger}")

    CORPUS.mkdir(parents=True, exist_ok=True)
    failures = 0
    for size in sorted(manifests):
        path = CORPUS / f"manifest_{size}.txt"
        text = "".join(f"{i}\n" for i in manifests[size])

        if args.check:
            if not path.exists():
                print(f"  manifest_{size}: MISSING")
                failures += 1
            elif path.read_text(encoding="utf-8") != text:
                print(f"  manifest_{size}: DIFFERS from what this seed produces")
                failures += 1
            else:
                print(f"  manifest_{size}: ok")
            continue

        if path.exists() and path.read_text(encoding="utf-8") != text:
            sys.exit(f"{path} already exists with different content -- manifests are"
                     " immutable; changing one invalidates the benchmarks recorded"
                     " against it")
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"  wrote {path.name}  ({size} books)")

    if args.check:
        return 1 if failures else 0

    print("\nManifests are now immutable. Every benchmark reads them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
