"""
Control files.  SPEC.md §2.2, §2.4; docs/STAGE1.md Part 5.

State tracking for the control layer (issue #5): which books are downloaded,
which are indexed, which failed and why -- plus the `run.lock` single-writer
guard.  `control-step` and `reconcile` live in pipeline.py, because they need
the datalake and the index; this module needs neither.

    control/downloaded_books.txt   one id per line
    control/indexed_books.txt      one id per line
    control/failed_books.txt       <id>\\t<REASON>\\t<ISO8601>
    control/run.lock               held by the one process writing the workspace

Every append is flushed and fsynced before it counts (SPEC.md §2.4): after a
crash an id is either fully recorded or not recorded at all, never half a line
that the next run would misread.  The sets are read once at start-up and kept
in memory, because `download --manifest` asks "already have it?" once per book.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["StateTracker", "WorkspaceLock", "WorkspaceLocked", "utc_now_iso"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateTracker:
    DOWNLOADED = "downloaded_books.txt"
    INDEXED = "indexed_books.txt"
    FAILED = "failed_books.txt"

    def __init__(self, workspace: str | Path) -> None:
        self.dir = Path(workspace) / "control"
        # download --workers N marks books from several threads.
        self._lock = threading.Lock()
        self._downloaded = self._read_ids(self.DOWNLOADED)
        self._indexed = self._read_ids(self.INDEXED)
        self._failed = self._read_ids(self.FAILED)

    # ---------------------------------------------------------------- reading

    def _read_ids(self, name: str) -> set[int]:
        path = self.dir / name
        if not path.exists():
            return set()
        ids = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            token = line.split("\t", 1)[0].strip()
            if token.isdigit():
                ids.add(int(token))
        return ids

    def is_downloaded(self, book_id: int) -> bool:
        return book_id in self._downloaded

    def is_indexed(self, book_id: int) -> bool:
        return book_id in self._indexed

    def downloaded(self) -> list[int]:
        return sorted(self._downloaded)

    def indexed(self) -> list[int]:
        return sorted(self._indexed)

    def is_failed(self, book_id: int) -> bool:
        return book_id in self._failed

    def ready_to_index(self) -> list[int]:
        """Downloaded but not yet indexed, ascending."""
        return sorted(self._downloaded - self._indexed)

    # ---------------------------------------------------------------- writing

    def _append(self, name: str, lines: list[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = "".join(f"{line}\n" for line in lines).encode("utf-8")
        with open(self.dir / name, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())

    def mark_downloaded(self, book_id: int) -> None:
        """Call only after every artifact of the book is renamed (SPEC.md §2.4)."""
        with self._lock:
            if book_id in self._downloaded:  # invariant I1: no duplicates
                return
            self._append(self.DOWNLOADED, [str(book_id)])
            self._downloaded.add(book_id)

    def mark_indexed(self, book_ids: list[int]) -> None:
        """Call only after the index batch holding these books is committed."""
        with self._lock:
            new = [i for i in dict.fromkeys(book_ids) if i not in self._indexed]
            if not new:
                return
            self._append(self.INDEXED, [str(i) for i in new])
            self._indexed.update(new)

    def mark_failed(self, book_id: int, reason: str, when: str | None = None) -> None:
        with self._lock:
            self._append(self.FAILED, [f"{book_id}\t{reason}\t{when or utc_now_iso()}"])
            self._failed.add(book_id)

    # ------------------------------------------------------------- reconcile

    def rewrite(self, downloaded: list[int], indexed: list[int]) -> None:
        """Replace both files atomically (used by `reconcile` only)."""
        from datalake.atomic import atomic_write

        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            atomic_write(self.dir / self.DOWNLOADED, "".join(f"{i}\n" for i in downloaded))
            atomic_write(self.dir / self.INDEXED, "".join(f"{i}\n" for i in indexed))
            self._downloaded, self._indexed = set(downloaded), set(indexed)


class WorkspaceLocked(Exception):
    """Another process holds control/run.lock (exit code 4, SPEC.md §1)."""


class WorkspaceLock:
    """Single-writer guard on control/run.lock.

    An OS lock on an open file, not "the file exists": the operating system
    releases it when the process dies, however it dies.  A lock file that only
    had to exist would outlive a SIGKILL and block the restart forever --
    exactly the case invariant I3 tests.
    """

    def __init__(self, workspace: str | Path) -> None:
        self.path = Path(workspace) / "control" / "run.lock"
        self._fh = None

    def __enter__(self) -> "WorkspaceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise WorkspaceLocked(f"{self.path} is held by another process") from exc
        self._fh = fh
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
