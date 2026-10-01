"""Integration checks for the proposed persisted ingestion contract."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli
from pipeline import make_storage, parse_now
from datamart.metadata import MetadataStore
from control_layer import WorkspaceLock

STAMP = "2026-01-01T07:30:11Z"


def run(ws, layout, *args):
    return cli.main(["--workspace", str(ws), "--datalake-layout", layout, *args])


@pytest.mark.parametrize("layout", ["book", "hash", "time"])
def test_metadata_roundtrip_and_idempotency(tmp_path, layout):
    storage = make_storage(layout, tmp_path, parse_now(STAMP))
    header, body = storage.write(42, "Title: Café\nLanguage: English\n", "café\n")
    metrics = tmp_path / "metrics.jsonl"
    assert run(tmp_path, layout, "--metrics-out", str(metrics), "metadata", "--all", "--batch-size", "1") == 0
    with MetadataStore(tmp_path) as db:
        record = db.by_id(42)
    assert record["ingested_at"] == STAMP
    assert record["language"] == "en"
    assert record["body_bytes"] == len("café\n".encode())
    assert record["sha256"] == hashlib.sha256("café\n".encode()).hexdigest()
    assert record["header_path"] == header and record["body_path"] == body
    meta = (tmp_path / body).with_name("meta.json")
    if layout == "book":
        assert json.loads(meta.read_text(encoding="utf-8")) == record
        before = meta.stat().st_mtime_ns
    assert run(tmp_path, layout, "--metrics-out", str(metrics), "metadata", "--book-id", "42") == 0
    records = [json.loads(line) for line in metrics.read_text(encoding="utf-8").splitlines()]
    assert records[0]["aux"]["docs_written"] == 1
    assert records[1]["aux"]["docs_written"] == 0
    if layout == "book":
        assert meta.stat().st_mtime_ns == before


def test_book_metadata_exists_before_metadata_command(tmp_path):
    storage = make_storage("book", tmp_path, parse_now(STAMP))
    storage.write(7, "Title: Test\n", "body\n")
    assert json.loads((tmp_path / "datalake/books/7/meta.json").read_text(encoding="utf-8"))["ingested_at"] == STAMP


def test_legacy_workspace_requires_receipt(tmp_path):
    folder = tmp_path / "datalake/books/7"
    folder.mkdir(parents=True)
    (folder / "header.txt").write_text("Title: Test\n")
    (folder / "body.txt").write_text("body\n")
    assert run(tmp_path, "book", "metadata", "--all") == 1
    assert not (tmp_path / "datamarts/metadata.db").exists()


def test_missing_book(tmp_path):
    assert run(tmp_path, "book", "metadata", "--book-id", "7") == 3


def test_empty_workspace(tmp_path):
    assert run(tmp_path, "book", "metadata", "--all") == 0
    assert not (tmp_path / "datamarts/metadata.db").exists()


def test_invalid_batch(tmp_path):
    assert run(tmp_path, "book", "metadata", "--all", "--batch-size", "0") == 2


def test_metadata_respects_writer_lock(tmp_path):
    with WorkspaceLock(tmp_path):
        assert run(tmp_path, "book", "metadata", "--all") == 4


def test_split_creates_receipt_and_book_metadata(tmp_path):
    raw = tmp_path / "raw/7.txt"
    raw.parent.mkdir()
    raw.write_text("Title: Test\n*** START OF THE PROJECT GUTENBERG EBOOK\nbody\n*** END OF THE PROJECT GUTENBERG EBOOK\n")
    assert run(tmp_path, "book", "--now", STAMP, "split", "--book-id", "7") == 0
    assert run(tmp_path, "book", "metadata", "--book-id", "7") == 0
    with MetadataStore(tmp_path) as db:
        assert db.by_id(7)["ingested_at"] == STAMP


@pytest.mark.parametrize("payload", ['{', '{"header_path":"wrong","body_path":"wrong","ingested_at":"bad"}'])
def test_invalid_receipt_fails_before_database_creation(tmp_path, payload):
    from datalake.ingestion import receipt_path
    make_storage("book", tmp_path, parse_now(STAMP)).write(7, "Title: Test\n", "body\n")
    receipt_path(tmp_path, "book", 7).write_text(payload)
    assert run(tmp_path, "book", "metadata", "--all") == 1
    assert not (tmp_path / "datamarts/metadata.db").exists()


def test_book_json_repaired_without_replacing_database_record(tmp_path):
    make_storage("book", tmp_path, parse_now(STAMP)).write(7, "Title: Test\n", "body\n")
    assert run(tmp_path, "book", "metadata", "--all") == 0
    meta = tmp_path / "datalake/books/7/meta.json"
    meta.unlink()
    assert run(tmp_path, "book", "metadata", "--all") == 0
    with MetadataStore(tmp_path) as db:
        assert json.loads(meta.read_text(encoding="utf-8")) == db.by_id(7)

def test_ingestion_book_metadata_has_exact_spec_bytes(tmp_path):
    storage = make_storage("book", tmp_path, parse_now(STAMP))
    header, body = storage.write(
        7, "Title: Café\nLanguage: English\n", "café\n"
    )

    expected = {
        "book_id": 7,
        "title": "Café",
        "author": None,
        "language": "en",
        "release_date": None,
        "header_path": header,
        "body_path": body,
        "body_bytes": len("café\n".encode("utf-8")),
        "sha256": hashlib.sha256("café\n".encode("utf-8")).hexdigest(),
        "ingested_at": STAMP,
    }
    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    meta = tmp_path / "datalake/books/7/meta.json"
    assert meta.read_bytes() == expected_bytes