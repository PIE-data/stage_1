"""
The pipeline commands of the CLI: download, split, index, lookup, scan-new,
control-step, reconcile.
SPEC.md §1, §2, §4, §6.  Issue #60.

cli.py parses the flags and dispatches here; this module wires together the
pieces that already exist and are tested on their own:

    datalake.downloader   fetch with retries              (SPEC.md §2.1)
    datalake.splitter     markers + cleaning              (SPEC.md §2.2-2.3)
    datalake.*_storage    the three layouts               (SPEC.md §4)
    datamart.index_*      the three index backends        (SPEC.md §6)
    core.control_layer    downloaded / indexed / failed,
                          run.lock                        (SPEC.md §2.4)

The shared behaviour of these commands -- exit codes, what `lookup` prints,
the raw cache, the positions rule -- is SPEC.md §1.2; this file follows it.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from control_layer import StateTracker
from datalake.atomic import atomic_write
from datalake.batch_storage import BatchBasedStorage
from datalake.book_storage import BookBasedStorage
from datalake.splitter import MarkersNotFound, split_text
from datalake.time_storage import TimeBasedStorage
from index_base import open_index
from tokenizer import load_stopwords, tokenize

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- helpers


def parse_now(value: str | None) -> datetime | None:
    """--now <ISO8601>.  A value without a zone is taken as UTC (SPEC.md §4)."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def make_storage(layout: str, workspace: Path, now: datetime | None = None):
    if layout == "time":
        return TimeBasedStorage(workspace, now=now)
    if layout == "book":
        return BookBasedStorage(workspace)
    if layout == "hash":
        return BatchBasedStorage(workspace)
    raise ValueError(f"unknown datalake layout: {layout!r}")


def raw_path(workspace: Path, book_id: int) -> Path:
    """SPEC.md §1.2: the decoded download, kept for `split`."""
    return workspace / "raw" / f"{book_id}.txt"


def read_manifest(path: str | Path) -> list[int]:
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            ids.append(int(token))
    return ids


SETTINGS_FILE = "index_settings.json"


def _settings_path(workspace: Path) -> Path:
    return workspace / "datamarts" / SETTINGS_FILE


def index_positions(workspace: Path, backend: str) -> bool | None:
    """Was this backend's index built with positions?  None if unknown."""
    path = _settings_path(workspace)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get(backend, {}).get("positions")


def _record_positions(workspace: Path, backend: str, positions: bool) -> None:
    path = _settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[backend] = {"positions": positions}
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------- download


def fetch_one(workspace: Path, storage, tracker: StateTracker, book_id: int,
              source_base: str) -> str | None:
    """Download, cache, split and store one book.  None, or the failure REASON.

    The caller records failures, so a thread pool never writes failed_books.txt
    from several threads at once.
    """
    # Imported here: `requests` costs start-up time that `query` and `lookup`
    # should not pay -- E2 and E7 would measure it.
    from datalake.downloader import download

    try:
        raw = download(book_id, source_base=source_base)
    except FileNotFoundError:
        return "NOT_FOUND"
    except Exception:  # retries exhausted, connection refused, ...
        return "DOWNLOAD_ERROR"
    atomic_write(raw_path(workspace, book_id), raw)  # SPEC.md §1.2
    try:
        header, body = split_text(raw)
    except MarkersNotFound:
        return "NO_MARKERS"  # nothing written to the datalake (SPEC.md §2.2)
    storage.write(book_id, header, body)
    tracker.mark_downloaded(book_id)  # only after both renames (SPEC.md §2.4)
    return None


def cmd_download(args, aux: dict) -> int:
    workspace = Path(args.workspace)
    if args.workers < 1:
        print("--workers must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        now = parse_now(args.now)
    except ValueError:
        print(f"--now is not ISO8601: {args.now!r}", file=sys.stderr)
        return EXIT_USAGE

    ids = [args.book_id] if args.book_id is not None else read_manifest(args.manifest)
    tracker = StateTracker(workspace)
    storage = make_storage(args.datalake_layout, workspace, now)
    todo = [i for i in dict.fromkeys(ids) if not tracker.is_downloaded(i)]

    def one(book_id: int) -> tuple[int, str | None]:
        return book_id, fetch_one(workspace, storage, tracker, book_id, args.source_base)

    if args.workers == 1:
        results = [one(i) for i in todo]
    else:
        # A bounded pool: `workers` requests in flight, never the whole
        # manifest at once (SPEC.md §9, concurrency fairness note).
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(one, todo))

    failures = [(i, reason) for i, reason in results if reason]
    for book_id, reason in failures:
        tracker.mark_failed(book_id, reason)

    aux.update(docs_processed=len(todo) - len(failures),
               docs_skipped=len(ids) - len(todo), docs_failed=len(failures))
    print(f"downloaded {len(todo) - len(failures)}, skipped {len(ids) - len(todo)} "
          f"already present, failed {len(failures)}", file=sys.stderr)
    for book_id, reason in failures:
        print(f"  {book_id}\t{reason}", file=sys.stderr)

    if any(reason == "DOWNLOAD_ERROR" for _, reason in failures):
        return EXIT_ERROR
    return EXIT_NOT_FOUND if failures else EXIT_OK


# ------------------------------------------------------------------ split


def cmd_split(args, aux: dict) -> int:
    """Re-split the cached raw file offline (SPEC.md §1.2)."""
    workspace = Path(args.workspace)
    try:
        now = parse_now(args.now)
    except ValueError:
        print(f"--now is not ISO8601: {args.now!r}", file=sys.stderr)
        return EXIT_USAGE
    source = raw_path(workspace, args.book_id)
    if not source.exists():
        print(f"no cached raw file for book {args.book_id}: {source}", file=sys.stderr)
        return EXIT_NOT_FOUND

    tracker = StateTracker(workspace)
    try:
        header, body = split_text(source.read_bytes().decode("utf-8"))
    except MarkersNotFound:
        tracker.mark_failed(args.book_id, "NO_MARKERS")
        print(f"book {args.book_id}: markers not found", file=sys.stderr)
        return EXIT_NOT_FOUND
    storage = make_storage(args.datalake_layout, workspace, now)
    storage.write(args.book_id, header, body)
    tracker.mark_downloaded(args.book_id)
    aux.update(body_bytes=len(body.encode("utf-8")))
    return EXIT_OK


# ------------------------------------------------------------------ index

STOPWORDS = Path(__file__).resolve().parents[2] / "spec" / "stopwords_en.txt"


def index_books(workspace: Path, backend: str, positions: bool, storage,
                tracker: StateTracker, ids: list[int], batch_size: int
                ) -> tuple[int, list[int]]:
    """Index `ids` in batches.  Returns (indexed, missing from the datalake)."""
    stopwords = load_stopwords(STOPWORDS)
    index = open_index(backend, workspace, positions=positions)
    _record_positions(workspace, backend, positions)
    indexed, missing = 0, []
    try:
        for start in range(0, len(ids), batch_size):
            chunk = ids[start:start + batch_size]
            done = []
            with index.batch() as batch:
                for book_id in chunk:
                    paths = storage.lookup(book_id)
                    if paths is None:
                        missing.append(book_id)
                        continue
                    body = (workspace / paths[1]).read_bytes().decode("utf-8")
                    batch.add_book(book_id, tokenize(body, stopwords))
                    done.append(book_id)
            # after the batch is committed, never before (crash safety, I3)
            tracker.mark_indexed(done)
            indexed += len(done)
    finally:
        index.close()
    return indexed, missing


def cmd_index(args, aux: dict) -> int:
    workspace = Path(args.workspace)
    backend = args.index_backend
    if backend not in ("json", "folder", "sqlite"):
        print(f"index is not implemented for backend {backend!r}", file=sys.stderr)
        return EXIT_USAGE
    if args.batch_size < 1:
        print("--batch-size must be >= 1", file=sys.stderr)
        return EXIT_USAGE

    existing = index_positions(workspace, backend)
    if existing is not None and existing != args.positions:
        print(f"the {backend} index in {workspace} was built "
              f"{'with' if existing else 'without'} --positions; "
              "rebuild it in a clean workspace to change that", file=sys.stderr)
        return EXIT_USAGE

    tracker = StateTracker(workspace)
    storage = make_storage(args.datalake_layout, workspace)
    ids = [args.book_id] if args.book_id is not None else tracker.ready_to_index()
    if args.book_id is not None and storage.lookup(args.book_id) is None:
        print(f"book {args.book_id} is not in the {args.datalake_layout} datalake",
              file=sys.stderr)
        return EXIT_NOT_FOUND

    indexed, missing = index_books(workspace, backend, args.positions, storage,
                                   tracker, ids, args.batch_size)
    aux.update(docs_processed=indexed, docs_missing=len(missing))
    print(f"indexed {indexed} book(s) into {backend}"
          + (f", {len(missing)} listed but missing from the datalake" if missing else ""),
          file=sys.stderr)
    return EXIT_NOT_FOUND if missing else EXIT_OK


# ----------------------------------------------------------------- lookup


def cmd_lookup(args, aux: dict) -> int:
    workspace = Path(args.workspace)
    storage = make_storage(args.datalake_layout, workspace)
    paths = storage.lookup(args.book_id)  # filesystem only, no cache (SPEC.md §4.1)
    if paths is None:
        print(f"book {args.book_id} not found in the {args.datalake_layout} datalake",
              file=sys.stderr)
        return EXIT_NOT_FOUND
    header_path, body_path = paths
    body = (workspace / body_path).read_bytes()  # E2 measures resolve + read
    aux.update(body_bytes=len(body))
    sys.stdout.write(f"{header_path}\t{body_path}\n")
    return EXIT_OK


# --------------------------------------------------------------- scan-new


def cmd_scan_new(args, aux: dict) -> int:
    workspace = Path(args.workspace)
    storage = make_storage(args.datalake_layout, workspace)
    tracker = StateTracker(workspace)
    present = set(storage.list_new(EPOCH))
    new = sorted(i for i in present if not tracker.is_indexed(i))
    aux.update(docs_in_datalake=len(present), docs_new=len(new))
    sys.stdout.write("".join(f"{i}\n" for i in new))
    return EXIT_OK


# ----------------------------------------------------------- control-step


def cmd_control_step(args, aux: dict) -> int:
    """The control layer: each iteration moves ONE book one stage forward.

    If a downloaded book is not yet indexed, index it (smallest id first);
    otherwise download the next candidate.  "Index pending first" is what
    makes a crashed run resume where it stopped instead of downloading on top
    of a backlog (docs/STAGE1.md Part 5).

    Candidates are the ids of --manifest in file order, or 1..--total-books
    ascending; ids already downloaded or failed are skipped.  No randomness:
    the three languages must pick the same books in the same order.
    """
    workspace = Path(args.workspace)
    backend = args.index_backend
    if backend not in ("json", "folder", "sqlite"):
        print(f"control-step is not implemented for backend {backend!r}", file=sys.stderr)
        return EXIT_USAGE
    if args.iterations < 1 or args.total_books < 1:
        print("--iterations and --total-books must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        now = parse_now(args.now)
    except ValueError:
        print(f"--now is not ISO8601: {args.now!r}", file=sys.stderr)
        return EXIT_USAGE

    positions = index_positions(workspace, backend)
    positions = True if positions is None else positions  # word-level by default
    tracker = StateTracker(workspace)
    storage = make_storage(args.datalake_layout, workspace, now)
    candidates = (read_manifest(args.manifest) if args.manifest
                  else range(1, args.total_books + 1))
    cursor = iter(candidates)

    downloaded = indexed = failed = 0
    for _ in range(args.iterations):
        pending = tracker.ready_to_index()
        if pending:
            n, _missing = index_books(workspace, backend, positions, storage,
                                      tracker, pending[:1], 1)
            indexed += n
            continue
        book_id = next((i for i in cursor
                        if not tracker.is_downloaded(i) and not tracker.is_failed(i)), None)
        if book_id is None:
            break  # nothing left to do: a complete corpus performs zero writes (I4)
        reason = fetch_one(workspace, storage, tracker, book_id, args.source_base)
        if reason:
            tracker.mark_failed(book_id, reason)
            failed += 1
        else:
            downloaded += 1

    aux.update(docs_downloaded=downloaded, docs_indexed=indexed, docs_failed=failed)
    print(f"control-step: downloaded {downloaded}, indexed {indexed}, failed {failed}",
          file=sys.stderr)
    return EXIT_OK


# -------------------------------------------------------------- reconcile


def cmd_reconcile(args, aux: dict) -> int:
    """Repair the control files from what is actually on disk.

    The recovery path for a crash between an artifact's rename and the append
    to downloaded_books.txt (SPEC.md §2.4):

      * downloaded = every id whose header AND body are in the datalake --
        adds books written but never recorded, drops ids whose artifacts are
        gone (I2), and removes duplicates (I1);
      * indexed = the old indexed list restricted to downloaded ids;
      * leftover *.part files from an interrupted atomic write are deleted.

    A book indexed but not yet marked when the crash hit is simply indexed
    again by the next run: re-indexing replaces postings, never duplicates them.
    """
    workspace = Path(args.workspace)
    storage = make_storage(args.datalake_layout, workspace)
    tracker = StateTracker(workspace)

    removed_parts = 0
    for sub in ("datalake", "raw", "datamarts", "control"):
        root = workspace / sub
        if root.exists():
            for part in root.rglob("*.part"):
                part.unlink(missing_ok=True)
                removed_parts += 1

    on_disk = sorted(i for i in set(storage.list_new(EPOCH)) if storage.lookup(i) is not None)
    before = set(tracker.downloaded())
    indexed = [i for i in tracker.indexed() if i in set(on_disk)]
    tracker.rewrite(on_disk, indexed)

    added = sorted(set(on_disk) - before)
    dropped = sorted(before - set(on_disk))
    aux.update(docs_added=len(added), docs_dropped=len(dropped), parts_removed=removed_parts)
    print(f"reconcile: {len(on_disk)} downloaded ({len(added)} recovered, "
          f"{len(dropped)} dropped), {len(indexed)} indexed, "
          f"{removed_parts} partial file(s) removed", file=sys.stderr)
    return EXIT_OK
