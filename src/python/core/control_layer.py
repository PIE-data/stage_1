"""
Control files.  SPEC.md §2.2, §2.4; docs/STAGE1.md Part 5.

This is the state-tracking half of the control layer (issue #5), the part the
CLI needs to be idempotent: which books are downloaded, which are indexed,
which failed and why.  `control-step`, `reconcile` and the `run.lock`
single-writer guard are the other half and arrive with the rest of #5.

    control/downloaded_books.txt   one id per line
    control/indexed_books.txt      one id per line
    control/failed_books.txt       <id>\\t<REASON>\\t<ISO8601>

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

__all__ = ["StateTracker", "utc_now_iso"]


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
