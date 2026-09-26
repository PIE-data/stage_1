import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple, override
from .base import DatalakeStorage
from .atomic import atomic_write

class TimeBasedStorage(DatalakeStorage):
    def __init__(self, workspace: Path | str,
                 now: datetime | None = None):
        self.workspace = Path(workspace)
        self.root = self.workspace / "datalake"
        self._now = now

    def _current_time(self) -> datetime:
        if self._now is not None:
            return self._now
        return datetime.now(timezone.utc)

    @override
    def write(self, book_id:int,
              header: str, body:str) -> Tuple[str, str]:
        
        timestamp = self._current_time()

        date_str = timestamp.strftime("%Y%m%d")
        time_str = timestamp.strftime("%H")

        target_dir = self.root / date_str / time_str

        header_path = target_dir / f"{book_id}.header.txt"
        body_path = target_dir / f"{book_id}.body.txt"

        atomic_write(header_path, header)
        atomic_write(body_path, body)

        return (
                str(header_path.relative_to(self.workspace)),
                str(body_path.relative_to(self.workspace))
            )

    @override
    def lookup(self, book_id: int) -> Tuple[str, str] | None:
        target_name = f"{book_id}.body.txt"

        for root, _, files in os.walk(self.root):
            if target_name in files:
                body_path = Path(root) / target_name
                header_path = Path(root) / f"{book_id}.header.txt"
                if header_path.exists():
                    return (
                        str(header_path.relative_to(self.workspace)),
                        str(body_path.relative_to(self.workspace))
                    )

        return None
    
    @override
    def list_new(self, since: datetime) -> Iterable[int]:
        root_str = str(self.root)
        if not os.path.exists(root_str):
            return

        since_date = since.strftime("%Y%m%d")
        since_hour = since.strftime("%H")

        with os.scandir(root_str) as dates:
            for date in dates:
                if date.is_dir() and date.name >= since_date:
                    with os.scandir(date.path) as hours:
                        for hour in hours:
                            if hour.is_dir() and (hour.name >= since_hour
                                                  or date.name > since_date):
                                with os.scandir(hour.path) as files:
                                    for file in files:
                                        if file.name.endswith(".body.txt"):
                                            yield int(file.name[:-9])
