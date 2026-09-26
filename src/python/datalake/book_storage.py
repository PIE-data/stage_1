import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple, override

from .atomic import atomic_write
from .base import DatalakeStorage

class BookBasedStorage(DatalakeStorage):
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = self.workspace / "datalake" / "books"

    @override
    def write(self, book_id: int,
          header: str, body: str) -> Tuple[str, str]:
        book_dir = self.root / str(book_id)

        header_path = book_dir / "header.txt"
        body_path = book_dir / "body.txt"

        atomic_write(header_path, header)
        atomic_write(body_path, body)

        return(
            str(header_path.relative_to(self.workspace)),
            str(body_path.relative_to(self.workspace))
        )

    @override
    def lookup(self, book_id: int) -> Tuple[str, str] | None:
        book_dir = self.root / str(book_id)

        header_path = book_dir / "header.txt"
        body_path = book_dir / "body.txt"

        if header_path.exists() and body_path.exists():
            return(
                str(header_path.relative_to(self.workspace)),
                str(body_path.relative_to(self.workspace))
            )
        return None

    @override
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

