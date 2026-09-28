import json
import logging
import sqlite3
import sys
import shutil
from pathlib import Path

import pytest


MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))

from metadata import MetadataStore, build_metadata_record, parse_header

FIXTURE_PATH = MODULE_DIR / "fixtures" / "metadata_headers.json"
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_parse_header(case, caplog):
    """Check each committed header against its explicit expected record."""
    with caplog.at_level(logging.WARNING, logger="metadata"):
        result = parse_header(case["header"])

    assert result == case["expected"]

    # Missing titles must produce both a fallback value and a warning.
    if case["name"] == "missing_title":
        assert any(
            record.getMessage() == "MISSING_TITLE"
            for record in caplog.records
        )

def test_build_metadata_record(tmp_path):
    """Verify metadata, relative paths, byte count and the body checksum."""
    book_dir = tmp_path / "datalake" / "books" / "123"
    book_dir.mkdir(parents=True)

    header_file = book_dir / "header.txt"
    body_file = book_dir / "body.txt"

    header_file.write_text(
        "Title: Example Book\nAuthor: Jane Doe\nLanguage: English\n",
        encoding="utf-8",
    )
    body_file.write_bytes(b"abc")

    record = build_metadata_record(
        book_id=123,
        workspace=tmp_path,
        header_path="datalake/books/123/header.txt",
        body_path="datalake/books/123/body.txt",
        ingested_at="2026-09-24T12:00:00Z",
    )

    assert record == {
        "book_id": 123,
        "title": "Example Book",
        "author": "Jane Doe",
        "language": "en",
        "release_date": None,
        "header_path": "datalake/books/123/header.txt",
        "body_path": "datalake/books/123/body.txt",
        "body_bytes": 3,
        "sha256": (
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad"
        ),
        "ingested_at": "2026-09-24T12:00:00Z",
    }

def test_metadata_store_persists_and_skips_identical_records(tmp_path):
    """Verify persistence and zero row changes on an identical rerun."""
    record = {
        "book_id": 123,
        "title": "Example Book",
        "author": "Jane Doe",
        "language": "en",
        "release_date": "2020-01-01",
        "header_path": "datalake/books/123/header.txt",
        "body_path": "datalake/books/123/body.txt",
        "body_bytes": 3,
        "sha256": (
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad"
        ),
        "ingested_at": "2026-09-24T12:00:00Z",
    }

    with MetadataStore(tmp_path) as store:
        assert store.upsert([record]) == 1
        assert store.by_id(123) == record

    # Reopen the database to verify that the record was committed to disk.
    with MetadataStore(tmp_path) as store:
        assert store.by_id(123) == record

        changes_before = store.connection.total_changes
        assert store.upsert([record]) == 0
        assert store.connection.total_changes == changes_before

        assert store.by_id(999) is None

@pytest.fixture
def sample_records():
    """Provide three metadata records with distinct query results."""
    books = [
        (1, "Island Adventure", "Jane Doe", "en"),
        (2, "Island Stories", "John Smith", "en"),
        (3, "Mountain Tales", "Jane Doe", "fr"),
    ]

    return [
        {
            "book_id": book_id,
            "title": title,
            "author": author,
            "language": language,
            "release_date": None,
            "header_path": f"datalake/books/{book_id}/header.txt",
            "body_path": f"datalake/books/{book_id}/body.txt",
            "body_bytes": 3,
            "sha256": (
                "ba7816bf8f01cfea414140de5dae2223"
                "b00361a396177a9cb410ff61f20015ad"
            ),
            "ingested_at": "2026-09-24T12:00:00Z",
        }
        for book_id, title, author, language in books
    ]


def test_metadata_queries(tmp_path, sample_records):
    """Verify Q1-Q4 and language filtering against known records."""
    with MetadataStore(tmp_path) as store:
        store.upsert(sample_records)

        # Q1: exact author match.
        assert {
            row["book_id"] for row in store.by_author("Jane Doe")
        } == {1, 3}
        assert store.by_author("Jane") == []

        # Q2: body path lookup by book ID.
        assert store.body_path_by_id(2) == "datalake/books/2/body.txt"
        assert store.body_path_by_id(999) is None

        # Q3: title prefix lookup.
        assert {
            row["book_id"] for row in store.by_title_prefix("Island")
        } == {1, 2}
        assert store.by_title_prefix("Unknown") == []

        # Q4: counts grouped by language.
        assert {
            row["language"]: row["count"]
            for row in store.language_counts()
        } == {"en": 2, "fr": 1}

        assert {
            row["book_id"] for row in store.by_language("fr")
        } == {3}


def test_metadata_update(tmp_path, sample_records):
    """Replace a changed record without creating duplicate book IDs."""
    with MetadataStore(tmp_path) as store:
        store.upsert(sample_records)

        updated = dict(sample_records[0], title="Revised Island Adventure")

        assert store.upsert([updated]) == 1
        assert store.by_id(1) == updated

        count = store.connection.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]
        assert count == 3


def test_metadata_batch_transactions(tmp_path, sample_records):
    """Commit once per batch and execute no writes for unchanged records."""
    with MetadataStore(tmp_path) as store:
        statements = []
        store.connection.set_trace_callback(statements.append)

        assert store.upsert(sample_records, batch_size=2) == 3

        commands = [statement.strip().upper() for statement in statements]
        assert sum(command.startswith("BEGIN") for command in commands) == 2
        assert commands.count("COMMIT") == 2

        statements.clear()
        assert store.upsert(sample_records, batch_size=2) == 0

        commands = [statement.strip().upper() for statement in statements]
        assert not any(
            command.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "BEGIN"))
            for command in commands
        )


def test_metadata_failed_batch_rolls_back(tmp_path, sample_records):
    """Keep earlier batches but roll back every write in a failed batch."""
    records = sample_records + [dict(sample_records[0], book_id=4, title=None)]

    with MetadataStore(tmp_path) as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert(records, batch_size=2)

    # The first batch survives; neither record from the second batch does.
    with MetadataStore(tmp_path) as store:
        assert store.by_id(1) == sample_records[0]
        assert store.by_id(2) == sample_records[1]
        assert store.by_id(3) is None
        assert store.by_id(4) is None


@pytest.mark.parametrize("batch_size", [0, -1])
def test_metadata_rejects_invalid_batch_size(tmp_path, batch_size):
    """Reject nonpositive batch sizes."""
    with MetadataStore(tmp_path) as store:
        with pytest.raises(ValueError, match="batch_size"):
            store.upsert([], batch_size=batch_size)

def test_metadata_workspace_can_be_copied(tmp_path):
    """Resolve stored paths correctly after copying the entire workspace."""
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    book_dir = original / "datalake" / "books" / "123"
    book_dir.mkdir(parents=True)

    header_file = book_dir / "header.txt"
    body_file = book_dir / "body.txt"

    header_file.write_text(
        "Title: Portable Book\nLanguage: English\n",
        encoding="utf-8",
    )
    body_file.write_bytes(b"Portable body\n")

    record = build_metadata_record(
        book_id=123,
        workspace=original,
        header_path=header_file,
        body_path=body_file,
        ingested_at="2026-09-24T12:00:00Z",
    )

    # Close SQLite before copying its files.
    with MetadataStore(original) as store:
        store.upsert([record])

    shutil.copytree(original, copied)
    shutil.rmtree(original)

    # The copied workspace must work without the original directory.
    with MetadataStore(copied) as store:
        restored = store.by_id(123)

        assert restored == record
        assert not Path(restored["header_path"]).is_absolute()
        assert not Path(restored["body_path"]).is_absolute()

        assert (copied / restored["body_path"]).read_bytes() == b"Portable body\n"
        assert "Portable Book" in (
            copied / restored["header_path"]
        ).read_text(encoding="utf-8")