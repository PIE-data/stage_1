# Node ingestion integration — proposed shared behaviour

Status: implemented in draft PR #97, pending specification agreement.
Related issue: #66.
Target specification: 1.1.5. SPEC_VERSION remains unchanged.

## Ingestion timestamps

Successful pipeline ingestion writes an atomic receipt at:

`control/ingestion/<layout>/<book_id>.json`

The receipt contains:

- `book_id`
- workspace-relative `header_path`
- workspace-relative `body_path`
- UTC `ingested_at`

The receipt is written after the datalake artifacts and before appending
the ID to `control/downloaded_books.txt`.

Offline splitting is a new ingestion and records its supplied ingestion
instant. Skipping an already downloaded book preserves its existing receipt.

## Metadata generation

Metadata generation reads the persisted receipt timestamp. It does not
derive timestamps from the current clock, filesystem mtime or hour buckets.
A later metadata `--now` does not replace the persisted timestamp.

All selected receipts and layout paths are validated before opening SQLite.
Missing books return exit code 3. Missing, malformed or mismatching receipts
return exit code 1.

`metadata --all` enumerates the selected layout and processes unique IDs in
ascending order. `--batch-size` defaults to 500.

Metadata generation holds the workspace writer lock. Empty selections
succeed without creating a metadata database.

Identical records cause no SQLite row writes. Identical book-layout
`meta.json` files are preserved. Missing or malformed `meta.json` files
can be repaired by rerunning metadata when artifacts and receipts are valid.

## Multiple time-layout copies

Lookup walks the filesystem and selects the complete header/body pair in
the newest UTC date/hour bucket. It never consults receipts or SQLite.
Older copies are retained.

Newest bucket does not necessarily mean latest write: a backdated
`--now` can produce a receipt that disagrees with lookup. Metadata rejects
that mismatch rather than silently using a different timestamp.

## Recovery and measurement limits

Artifacts, receipts, control files and SQLite are not one transaction.
Interrupted ingestion can leave artifacts without a valid receipt.
Historical timestamps are not fabricated. Re-splitting cached raw data
creates a new ingestion, not a recovery of its original timestamp.

The group must define receipt recovery and whether receipt storage belongs
in experiment E5.

Command metrics use a monotonic clock. Peak RSS is left to the runner.
Unmeasured auxiliary counters are null.

On Windows, the atomic writer can encounter unsupported directory fsync.
Passing functional tests does not establish full power-loss durability.

## Validation status

The local Windows Node suite passed 207 tests before this documentation
change, including:

- splitter byte parity against Python output for the 20 golden books;
- metadata parsing against the 10 committed fixtures;
- filesystem operations for all three layouts;
- native lock contention and release after forced process termination;
- local HTTP ingestion and CLI exit codes;
- metadata idempotency, transaction rollback and receipt validation;
- JSONL command metrics.

This does not establish full engine conformance. The canonical index gate,
all-engine crash-and-resume invariants and specification approval remain
separate requirements.

Normative adoption requires the project's spec-change process and a
SPEC_VERSION update. This draft does not claim that approval.