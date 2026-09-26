import logging
import re
import hashlib
import sqlite3
from datetime import date
from pathlib import Path


LOGGER = logging.getLogger(__name__)

LANGUAGE_MAP_PATH = (
    Path(__file__).resolve().parents[3] / "spec" / "language_map.txt"
)

# Use an explicit English month table to avoid locale-dependent parsing.
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

FIELDS = {
    "title": "title",
    "author": "author",
    "language": "language",
    "release date": "release_date",
}


def normalize_whitespace(value: str) -> str:
    """Collapse whitespace runs and remove surrounding whitespace."""
    return " ".join(value.split())


def load_language_map(path: Path = LANGUAGE_MAP_PATH) -> dict[str, str]:
    """Read language mappings from the shared specification asset."""
    mapping = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        name, code = line.split("=", 1)
        mapping[name.strip().lower()] = code.strip()

    return mapping


def parse_release_date(value: str | None) -> str | None:
    """Parse an English date, optionally followed by an eBook annotation."""
    if not value:
        return None

    match = re.fullmatch(
        r"([A-Za-z]+) (\d{1,2}), (\d{4})(?: \[.*\])?",
        value,
    )
    if not match:
        return None

    month_name, day, year = match.groups()
    month = MONTHS.get(month_name.lower())

    if month is None:
        return None

    try:
        return date(int(year), month, int(day)).isoformat()
    except ValueError:
        return None


def parse_header(header: str) -> dict[str, str | None]:
    """Extract descriptive metadata according to SPEC.md section 5.2."""
    fields = {}
    current_field = None

    for line in header.splitlines():
        # Indented lines extend the immediately preceding recognized field.
        if line[:1].isspace() and current_field is not None:
            continuation = line.strip()
            if continuation:
                fields[current_field] += " " + continuation
            else:
                current_field = None
            continue

        current_field = None
        match = re.match(r"^([^:\s][^:]*):\s*(.*)$", line)

        if not match:
            continue

        field_name, value = match.groups()
        field_name = normalize_whitespace(field_name).lower()
        current_field = FIELDS.get(field_name)

        if current_field is not None:
            fields[current_field] = value

    fields = {
        key: normalize_whitespace(value)
        for key, value in fields.items()
    }

    title = fields.get("title")
    if not title:
        title = "Unknown"
        LOGGER.warning("MISSING_TITLE")

    author = fields.get("author")
    if author is not None:
        # Remove a trailing lifespan while preserving surname-first names.
        author = re.sub(
            r",\s*\d{4}\s*[-–]\s*\d{4}\s*$",
            "",
            author,
        ).strip()

    language = fields.get("language")
    if language is not None:
        language = language.lower()
        language = load_language_map().get(language, language)

    return {
        "title": title,
        "author": author,
        "language": language,
        "release_date": parse_release_date(fields.get("release_date")),
    }

def build_metadata_record(
    book_id: int,
    workspace: str | Path,
    header_path: str | Path,
    body_path: str | Path,
    ingested_at: str,
) -> dict:
    """Build a complete metadata record from stored header and body files."""
    root = Path(workspace).resolve()

    # Relative input paths are interpreted from the workspace root.
    header_file = (root / Path(header_path)).resolve()
    body_file = (root / Path(body_path)).resolve()

    # Reject files outside the workspace and store portable relative paths.
    relative_header = header_file.relative_to(root).as_posix()
    relative_body = body_file.relative_to(root).as_posix()

    header = header_file.read_text(encoding="utf-8", errors="replace")
    record = parse_header(header)

    # Hash the stored body bytes without decoding or changing line endings.
    body_hash = hashlib.sha256()
    body_bytes = 0

    with body_file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            body_hash.update(chunk)
            body_bytes += len(chunk)

    record.update({
        "book_id": book_id,
        "header_path": relative_header,
        "body_path": relative_body,
        "body_bytes": body_bytes,
        "sha256": body_hash.hexdigest(),
        "ingested_at": ingested_at,
    })

    return record

class MetadataStore:
    """Store book metadata in SQLite according to SPEC.md section 5."""

    COLUMNS = (
        "book_id",
        "title",
        "author",
        "language",
        "release_date",
        "header_path",
        "body_path",
        "body_bytes",
        "sha256",
        "ingested_at",
    )

    def __init__(self, workspace: str | Path):
        database_path = Path(workspace) / "datamarts" / "metadata.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row

        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")

        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                book_id      INTEGER PRIMARY KEY,
                title        TEXT NOT NULL,
                author       TEXT,
                language     TEXT,
                release_date TEXT,
                header_path  TEXT NOT NULL,
                body_path    TEXT NOT NULL,
                body_bytes   INTEGER NOT NULL,
                sha256       TEXT NOT NULL,
                ingested_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_books_author
                ON books(author);

            CREATE INDEX IF NOT EXISTS idx_books_language
                ON books(language);

            CREATE INDEX IF NOT EXISTS idx_books_title
                ON books(title);
        """)

    def close(self) -> None:
        """Close the database connection."""
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def upsert(self, records: list[dict], batch_size: int = 500) -> int:
        """Insert changed records in batches and return the write count."""
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        columns = ", ".join(self.COLUMNS)
        placeholders = ", ".join("?" for _ in self.COLUMNS)
        statement = (
            f"INSERT OR REPLACE INTO books ({columns}) "
            f"VALUES ({placeholders})"
        )
        written = 0

        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]

            # Commit each batch together; roll it back if any write fails.
            with self.connection:
                for record in batch:
                    values = tuple(record[column] for column in self.COLUMNS)
                    existing = self.by_id(record["book_id"])

                    # Skip identical records before executing a write.
                    if existing is not None:
                        previous = tuple(
                            existing[column] for column in self.COLUMNS
                        )
                        if previous == values:
                            continue

                    self.connection.execute(statement, values)
                    written += 1

        return written

    def by_id(self, book_id: int) -> dict | None:
        """Return the complete record for a book, if present."""
        row = self.connection.execute(
            "SELECT * FROM books WHERE book_id = ?",
            (book_id,),
        ).fetchone()

        return dict(row) if row is not None else None

    def by_author(self, author: str) -> list[dict]:
        """Q1: return books whose author matches exactly."""
        rows = self.connection.execute(
            "SELECT * FROM books WHERE author = ?",
            (author,),
        ).fetchall()

        return [dict(row) for row in rows]

    def body_path_by_id(self, book_id: int) -> str | None:
        """Q2: return the stored body path for a book."""
        row = self.connection.execute(
            "SELECT body_path FROM books WHERE book_id = ?",
            (book_id,),
        ).fetchone()

        return row["body_path"] if row is not None else None

    def by_title_prefix(self, prefix: str) -> list[dict]:
        """Q3: match titles using the specification's LIKE query."""
        rows = self.connection.execute(
            "SELECT * FROM books WHERE title LIKE ? || '%'",
            (prefix,),
        ).fetchall()

        return [dict(row) for row in rows]

    def language_counts(self) -> list[dict]:
        """Q4: count books grouped by language."""
        rows = self.connection.execute(
            "SELECT language, COUNT(*) AS count FROM books GROUP BY language"
        ).fetchall()

        return [dict(row) for row in rows]

    def by_language(self, language: str) -> list[dict]:
        """Return books whose language matches exactly."""
        rows = self.connection.execute(
            "SELECT * FROM books WHERE language = ?",
            (language,),
        ).fetchall()

        return [dict(row) for row in rows]