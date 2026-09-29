"""Proposed ingestion receipt contract; see docs/METADATA_INTEGRATION_PROPOSAL.md."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .atomic import atomic_write
from datamart.metadata import build_metadata_record


def receipt_path(workspace, layout, book_id):
    return Path(workspace) / "control" / "ingestion" / layout / f"{book_id}.json"


def finish_ingestion(workspace, layout, book_id, paths, instant):
    """Persist the instant after artifacts, before mark_downloaded is called."""
    stamp = instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if layout == "book":
        record = build_metadata_record(book_id, workspace, *paths, stamp)
        write_book_metadata(workspace, paths[1], record)
    receipt = dict(header_path=paths[0], body_path=paths[1], ingested_at=stamp)
    atomic_write(receipt_path(workspace, layout, book_id),
                 json.dumps(receipt, sort_keys=True) + "\n")


def write_book_metadata(workspace, body_path, record):
    target = (Path(workspace) / body_path).with_name("meta.json")
    text = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    # Avoid replacing an identical artifact on repeated metadata runs.
    if not target.exists() or target.read_bytes() != text.encode("utf-8"):
        atomic_write(target, text)


def read_ingested_at(workspace, layout, book_id, paths):
    path = receipt_path(workspace, layout, book_id)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (receipt["header_path"], receipt["body_path"]) != tuple(paths):
            raise ValueError("receipt paths do not match the selected artifacts")
        stamp = receipt["ingested_at"]
        instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("ingestion timestamp has no timezone")
        return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"book {book_id}: missing or invalid ingestion receipt: {path}") from exc
