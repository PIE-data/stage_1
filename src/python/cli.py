#!/usr/bin/env python3
"""
Command-line surface for the Python implementation.  SPEC.md §1.

This file is the ONLY entry point the benchmark runner and the conformance
script are allowed to call, in any language.  Everything it does must be
reproducible from SPEC.md alone: the Node and Go ports are written from the
specification, never from this source, so anything decided here and not
written down there is a divergence waiting to happen.

Implemented today
-----------------
    version             SPEC_VERSION + build info
    query               issue #21 / task T24
    export-canonical    task T23, driven from an index already on disk

Declared but not implemented
----------------------------
The remaining commands are listed so the surface is complete and the exit
codes stay honest: they fail with code 1 and name the issue that owns them,
rather than pretending to be absent.  Filling one in is a matter of wiring an
existing module into `_dispatch`.

Two gaps in SPEC.md that this file had to settle
------------------------------------------------
1.  §1 defines the query FLAGS but not the query OUTPUT.  "Identical id list
    from all three backends" (T24) cannot be checked byte for byte without a
    fixed format, so this is the format, and it needs to go into the spec
    before the ports are written:

        stdout carries the matching book ids, one per line, ASCENDING,
        LF endings, nothing else -- no header, no count, no ranking.
        Zero matches prints nothing and exits 0.
        Anything human-readable goes to stderr.

    Ascending id, not relevance: Stage 1 has no scoring function, and any
    other order would differ between backends by accident.

2.  A query term is normalised and filtered by the SAME pipeline as the
    documents (§3.1–§3.3), otherwise "The" and "Whale" could never match an
    index that stores "whale".  Terms that the filter drops -- stop words,
    single characters, all-digit strings -- are IGNORED, not treated as
    unmatchable: a query of nothing but stop words returns no results and
    exits 0.  This too belongs in the spec.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _sub in ("core", "datalake", "datamart"):
    sys.path.insert(0, str(REPO / "src" / "python" / _sub))

from canonical import export_canonical  # noqa: E402
from index_base import open_index  # noqa: E402
from tokenizer import load_stopwords, tokenize  # noqa: E402

SPEC_VERSION_FILE = REPO / "spec" / "SPEC_VERSION"

# The specification this code implements.  SPEC.md line 10: an implementation
# refuses to run when this differs from spec/SPEC_VERSION, so a spec change
# that nobody ported fails loudly instead of producing subtly different output.
SUPPORTED_SPEC_VERSION = "1.1.1"
STOPWORDS_FILE = REPO / "spec" / "stopwords_en.txt"

LAYOUTS = ("time", "book", "hash")
BACKENDS = ("json", "folder", "sqlite", "mongo")

# Commands that exist in SPEC.md §1 but not yet in this repository, and who
# owns them.  Keeping them here makes `engine <cmd> --help` honest.
PENDING = {
    "download": "issue #1 (downloader)",
    "split": "issue #1 (downloader + splitter wiring)",
    "metadata": "issue #4 (metadata datamart)",
    "index": "issue #2 (datalake storages) -- needs bodies to read",
    "lookup": "issue #2 (datalake storages)",
    "scan-new": "issue #5 (control layer)",
    "control-step": "issue #5 (control layer)",
    "reconcile": "issue #5 (control layer)",
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------- query


def query_terms(raw: str) -> list[str]:
    """Normalise and filter the query string exactly like a document.

    Returns the surviving terms in the order given, duplicates removed.  The
    tokenizer does all the work (§3.1–§3.3); doing anything else here is how a
    query stops matching the index it queries.
    """
    stopwords = load_stopwords(STOPWORDS_FILE)
    seen: list[str] = []
    for term, _position in tokenize(raw, stopwords):
        if term not in seen:
            seen.append(term)
    return seen


def run_query(index, terms: list[str], mode: str, limit: int | None = None) -> list[int]:
    """Book ids matching `terms`, ascending.

    and -> intersection, or -> union.  An empty term list matches nothing in
    both modes: there is no such thing as "every book" here, because that
    would make a stop-word-only query return the whole corpus.
    """
    if not terms:
        return []

    sets = [{book_id for book_id, _tf, _pos in index.postings_of(term)} for term in terms]

    if mode == "and":
        result = set.intersection(*sets)
    else:
        result = set.union(*sets)

    ids = sorted(result)
    if limit is not None:
        ids = ids[:limit]
    return ids


def _index_exists(backend: str, workspace: Path) -> bool:
    """Is there an index of this backend in the workspace?

    Checked BEFORE opening, because the sqlite backend creates its database
    file on connect: without this, querying an empty workspace would silently
    answer "no results" instead of "you have not indexed anything".
    """
    marts = workspace / "datamarts"
    return {
        "json": (marts / "inverted_index.json").exists(),
        "folder": (marts / "inverted_index").is_dir(),
        "sqlite": (marts / "index.db").exists(),
    }.get(backend, False)


def cmd_query(args) -> int:
    workspace = Path(args.workspace)

    if args.limit is not None and args.limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    if args.index_backend not in ("json", "folder", "sqlite"):
        print(f"query is not implemented for backend {args.index_backend!r}", file=sys.stderr)
        return EXIT_USAGE
    if not _index_exists(args.index_backend, workspace):
        print(
            f"no {args.index_backend} index in {workspace} -- run `index --all` first",
            file=sys.stderr,
        )
        return EXIT_ERROR

    terms = query_terms(args.terms)
    dropped = [t for t in args.terms.split() if t]
    if not terms:
        print(
            f"every query term was filtered out ({len(dropped)} given): "
            "stop words, single characters and all-digit strings are never indexed",
            file=sys.stderr,
        )

    index = open_index(args.index_backend, workspace)
    try:
        ids = run_query(index, terms, args.mode, args.limit)
    finally:
        index.close()

    # stdout: ids only, one per line, LF.  See the module docstring.
    out = sys.stdout
    for book_id in ids:
        out.write(f"{book_id}\n")
    out.flush()

    print(
        f"{len(ids)} book(s) for [{' '.join(terms)}] mode={args.mode} "
        f"backend={args.index_backend}",
        file=sys.stderr,
    )
    return EXIT_OK


# ---------------------------------------------------------- other commands


def check_spec_version(version_file: Path = SPEC_VERSION_FILE) -> str | None:
    """None when the repository's spec is the one this code implements,
    otherwise the message explaining the mismatch."""
    try:
        found = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"cannot read {version_file}: {exc}"
    if found != SUPPORTED_SPEC_VERSION:
        return f"spec mismatch: this implementation supports {SUPPORTED_SPEC_VERSION}, found {found}"
    return None


def cmd_version(args) -> int:
    # stdout carries the version only, like the Node CLI, so a script can
    # compare the three implementations byte for byte; build info on stderr.
    print(SUPPORTED_SPEC_VERSION)
    print(
        f"stage-1-python | Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_export_canonical(args) -> int:
    workspace = Path(args.workspace)
    if args.index_backend not in ("json", "folder", "sqlite"):
        print(f"unknown index backend {args.index_backend!r}", file=sys.stderr)
        return EXIT_USAGE
    if not _index_exists(args.index_backend, workspace):
        print(f"no {args.index_backend} index in {workspace}", file=sys.stderr)
        return EXIT_ERROR

    index = open_index(args.index_backend, workspace)
    try:
        data = export_canonical(index, args.out)
    finally:
        index.close()
    print(f"{len(data)} bytes -> {args.out}", file=sys.stderr)
    return EXIT_OK


def cmd_pending(args) -> int:
    print(f"`{args.command}` is not implemented yet -- {PENDING[args.command]}", file=sys.stderr)
    return EXIT_ERROR


# ------------------------------------------------------------------ parsing


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="engine", description="Stage 1 search engine (Python)")

    # Global flags, SPEC.md §1.  They precede the command.
    p.add_argument("--workspace", required=True, help="root of datalake/datamarts/control")
    p.add_argument("--datalake-layout", choices=LAYOUTS, default="time")
    p.add_argument("--index-backend", choices=BACKENDS, default="json")
    p.add_argument("--metrics-out", default=None)
    p.add_argument("--log-level", choices=("error", "warn", "info", "debug"), default="info")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--now", default=None, help="ISO8601 override for the time layout")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version")

    q = sub.add_parser("query")
    q.add_argument("--terms", required=True, help='space-separated, e.g. "whale ship"')
    q.add_argument("--mode", choices=("and", "or"), required=True)
    q.add_argument("--limit", type=int, default=None)

    e = sub.add_parser("export-canonical")
    e.add_argument("--out", required=True)

    for name, owner in PENDING.items():
        # No flags declared: whatever the caller passes is accepted and
        # ignored, so a script written against SPEC.md §1 gets the honest
        # "not implemented yet, issue #N" instead of an argparse usage error
        # that looks like the command does not exist.
        sub.add_parser(name, help=f"not implemented -- {owner}")

    return p


def _dispatch(args) -> int:
    if args.command == "version":
        return cmd_version(args)
    if args.command == "query":
        return cmd_query(args)
    if args.command == "export-canonical":
        return cmd_export_canonical(args)
    return cmd_pending(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.command not in PENDING:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    # Every command, not only `version`: SPEC.md line 10 says refuse to RUN.
    problem = check_spec_version()
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_ERROR

    try:
        return _dispatch(args)
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
