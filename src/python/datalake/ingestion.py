"""Persisted ingestion receipts and derived book metadata (SPEC §1.2)."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .atomic import atomic_write
from datamart.metadata import build_metadata_record


def receipt_path(workspace, layout, book_id):
    return Path(workspace) / "control" / "ingestion" / layout / f"{book_id}.json"


def write_receipt(workspace, layout, book_id, header_path, body_path, instant):
    stamp = instant.astimezone(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")

    receipt = {
        "book_id": book_id,
        "header_path": header_path,
        "body_path": body_path,
        "ingested_at": stamp,
    }
    text = json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n"
    atomic_write(receipt_path(workspace, layout, book_id), text)


def finish_ingestion(workspace, layout, book_id, paths, instant):
    """Complete artifacts and persist the receipt before the control append."""
    instant = instant.astimezone(timezone.utc).replace(microsecond=0)
    stamp = instant.isoformat().replace("+00:00", "Z")

    if layout == "book":
        record = build_metadata_record(book_id, workspace, *paths, stamp)
        write_book_metadata(workspace, paths[1], record)

    write_receipt(workspace, layout, book_id, *paths, instant)


def write_book_metadata(workspace, body_path, record):
    target = (Path(workspace) / body_path).with_name("meta.json")

    # SPEC §5.1 defines the serialized key order.
    keys = (
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
    ordered = {key: record[key] for key in keys}
    text = json.dumps(
        ordered, ensure_ascii=False, separators=(",", ":")
    ) + "\n"
    expected = text.encode("utf-8")

    try:
        current = target.read_bytes()
    except OSError:
        current = None

    if current != expected:
        atomic_write(target, text)


def read_ingested_at(workspace, layout, book_id, paths):
    path = receipt_path(workspace, layout, book_id)

    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be an object")

        if (
            type(receipt.get("book_id")) is not int
            or receipt["book_id"] != book_id
        ):
            raise ValueError("receipt book ID does not match")

        if (receipt["header_path"], receipt["body_path"]) != tuple(paths):
            raise ValueError("receipt paths do not match the selected artifacts")

        stamp = receipt["ingested_at"]
        if not isinstance(stamp, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            stamp,
        ):
            raise ValueError("receipt timestamp must use whole UTC seconds")

        instant = datetime.strptime(
            stamp, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)

        if layout == "book":
            expected = (
                f"datalake/books/{book_id}/header.txt",
                f"datalake/books/{book_id}/body.txt",
            )
        elif layout == "hash":
            prefix = f"{book_id:06d}"
            directory = f"datalake/{prefix[:2]}/{prefix[2:4]}"
            expected = (
                f"{directory}/{book_id}.header.txt",
                f"{directory}/{book_id}.body.txt",
            )
        elif layout == "time":
            directory = f"datalake/{instant:%Y%m%d}/{instant:%H}"
            expected = (
                f"{directory}/{book_id}.header.txt",
                f"{directory}/{book_id}.body.txt",
            )
        else:
            raise ValueError("unknown datalake layout")

        if tuple(paths) != expected:
            raise ValueError("receipt paths do not match the layout")

        return stamp

    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"book {book_id}: missing or invalid ingestion receipt: {path}"
        ) from exc