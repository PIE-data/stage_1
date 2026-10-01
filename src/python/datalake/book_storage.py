from .ingestion import finish_ingestion
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple

from .atomic import atomic_write
from .base import DatalakeStorage

class BookBasedStorage(DatalakeStorage):
    def __init__(self, workspace: Path | str, now: datetime | None = None):
        self.workspace = Path(workspace)
        self._now = now
        self.root = self.workspace / "datalake" / "books"

    def write(self, book_id: int,
          header: str, body: str) -> Tuple[str, str]:
        book_dir = self.root / str(book_id)

        header_path = book_dir / "header.txt"
        body_path = book_dir / "body.txt"

        timestamp = self._now or datetime.now(timezone.utc)
        atomic_write(header_path, header)
        atomic_write(body_path, body)
        paths = (header_path.relative_to(self.workspace).as_posix(),
                 body_path.relative_to(self.workspace).as_posix())
        finish_ingestion(self.workspace, "book", book_id, paths, timestamp)

        return(
            header_path.relative_to(self.workspace).as_posix(),
            body_path.relative_to(self.workspace).as_posix()
        )

    def lookup(self, book_id: int) -> Tuple[str, str] | None:
        book_dir = self.root / str(book_id)

        header_path = book_dir / "header.txt"
        body_path = book_dir / "body.txt"

        if header_path.exists() and body_path.exists():
            return(
                header_path.relative_to(self.workspace).as_posix(),
                body_path.relative_to(self.workspace).as_posix()
            )
        return None

    def list_new(self, since: datetime) -> Iterable[int]:
        root_str = str(self.root)
        if not os.path.exists(root_str):
            return

        since_ts = since.timestamp()

        with os.scandir(root_str) as entries:
            for entry in entries:
                if entry.is_dir():
                    body_path = os.path.join(entry.path, "body.txt")
                    try:
                        if os.path.exists(body_path) and os.stat(body_path).st_mtime >= since_ts:
                            yield int(entry.name)
                    except ValueError:
                        continue

