from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol, Tuple

class DatalakeStorage(Protocol):
    """
    Common contract for all three datalake storage layouts.
    """

    def write(self, book_id: int,
              header: str, body: str) -> Tuple[str, str]:
        """
        Writes header and body to the datalake layout.
        Returns paths relative to the workspace root: (header_path, body_path).
        """
        ...

    def lookup(self, book_id: int) -> Tuple[str, str] | None:
        """
        Resolves the header and body paths for a book using only
        the filesystem.
        Returns paths relative to the workspace root: (header_path, body_path)
        or None if the book is not found.
        """

        ...

    def list_new(self, since: datetime) -> Iterable[int]:
        """
        Yields book IDs that were added or modified at or after 'since'.
        """
        
        ...

