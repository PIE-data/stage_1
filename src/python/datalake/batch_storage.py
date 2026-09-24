import os

from datetime import datetime
from pathlib import Path
from typing import Tuple, Iterable, override

from .atomic import atomic_write
from .base import DatalakeStorage

class BatchBasedStorage(DatalakeStorage):
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = self.workspace / "datalake"

    def _get_target_dir(self, book_id: int) -> Path:
        """Computes the 2-level digit prefix directory"""
        id6 = f"{book_id:06d}"
        return self.root / id6[0:2] / id6[2:4]

    @override
    def write(self, book_id: int, 
              header: str, body: str) -> Tuple[str, str]:
        target_dir = self._get_target_dir(book_id)

        header_path = target_dir / f"{book_id}.header.txt"
        body_path = target_dir / f"{book_id}.body.txt"

        atomic_write(header_path, header)
        atomic_write(body_path, body)
        
        return(
            str(header_path.relative_to(self.workspace)),
            str(body_path.relative_to(self.workspace))
        )

    @override
    def lookup(self, book_id: int) -> Tuple[str, str] | None:
        """ Direct path lookup """
        target_dir = self._get_target_dir(book_id)

        header_path = target_dir / f"{book_id}.header.txt"
        body_path = target_dir / f"{book_id}.body.txt"

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

        for dirpath, _, filenames in os.walk(root_str):
            for filename in filenames:
                if filename.endswith(".body.txt"):
                    filepath = os.path.join(dirpath, filename)
                    try:
                        if os.stat(filepath).st_mtime >= since_ts:
                            yield int(filename[:-9])
                    except ValueError:
                        continue
