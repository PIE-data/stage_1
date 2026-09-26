"""
The pipeline commands of the CLI: download, index, lookup, scan-new.
SPEC.md §1, §2, §4, §6.  Issue #60.

cli.py parses the flags and dispatches here; this module wires together the
pieces that already exist and are tested on their own:

    datalake.downloader   fetch with retries              (SPEC.md §2.1)
    datalake.splitter     markers + cleaning              (SPEC.md §2.2-2.3)
    datalake.*_storage    the three layouts               (SPEC.md §4)
    datamart.index_*      the three index backends        (SPEC.md §6)
    core.control_layer    downloaded / indexed / failed   (SPEC.md §2.4)

Decisions SPEC.md does not make and this file had to (listed so the Node and
Go ports can follow them, and so they can go into the spec):

  * download --manifest keeps going when a book fails: the failure is recorded
    in control/failed_books.txt and the run exits 3 at the end if any book
    was NOT_FOUND or NO_MARKERS, 1 if any failed for a network reason.
  * a book already in downloaded_books.txt is skipped without a request
    (invariant I4: re-running on a complete corpus performs zero writes).
  * lookup prints `<header_path>\\t<body_path>` (relative, forward slashes) and
    reads the body, so experiment E2 measures resolving AND reading.
  * index records whether the index was built with --positions in
    datamarts/index_settings.json; adding to an index built the other way is
    a usage error, and export-canonical reads the setting instead of assuming.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from control_layer import StateTracker
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


def cmd_download(args, aux: dict) -> int:
    # Imported here: `requests` costs start-up time that `query` and `lookup`
    # should not pay -- E2 and E7 would measure it.
    from datalake.downloader import download

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
        try:
            raw = download(book_id, source_base=args.source_base)
        except FileNotFoundError:
            return book_id, "NOT_FOUND"
        except Exception:  # retries exhausted, connection refused, ...
            return book_id, "DOWNLOAD_ERROR"
        try:
            header, body = split_text(raw)
        except MarkersNotFound:
            return book_id, "NO_MARKERS"  # nothing written (SPEC.md §2.2)
        storage.write(book_id, header, body)
        tracker.mark_downloaded(book_id)  # only after both renames (§2.4)
        return book_id, None

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


# ------------------------------------------------------------------ index


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

    stopwords = load_stopwords(Path(__file__).resolve().parents[2] / "spec" / "stopwords_en.txt")
    index = open_index(backend, workspace, positions=args.positions)
    _record_positions(workspace, backend, args.positions)
    indexed, missing = 0, []
    try:
        for start in range(0, len(ids), args.batch_size):
            chunk = ids[start:start + args.batch_size]
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
