# Python metadata integration — SPEC 1.1.6

This document supersedes the earlier proposal targeting SPEC 1.1.3.
The ingestion receipt contract is now normative in docs/SPEC.md §1.2.
This integration does not change SPEC.md or SPEC_VERSION.

## Implemented behaviour

- Storage writes header and body, plus complete meta.json for the book
  layout, before persisting the ingestion receipt.
- Receipts live at control/ingestion/<layout>/<book_id>.json.
  They contain book_id, header_path, body_path and ingested_at, in that
  order, as compact UTF-8 JSON followed by one LF.
- Ingestion timestamps use UTC whole seconds, with fractions truncated.
  The time layout derives its directory from the same instant.
  --now overrides ingestion time; split performs a new ingestion.
- Completed downloads are skipped without replacing their receipts.
- metadata --all enumerates the selected layout and sorts unique IDs.
  metadata --book-id selects one book.
- Metadata reads ingested_at only from receipts. It never substitutes
  the current clock, file modification time or --now.
- Missing selected books exit 3. Missing or invalid receipts, including
  mismatching IDs, paths or timestamps, exit 1.
  All selected receipts are validated before metadata writes.
- Metadata uses batched SQLite transactions, with --batch-size defaulting
  to 500. Identical records produce zero SQLite row writes.
  Identical book meta.json files are preserved.
- Metadata holds the workspace writer lock, exits 4 on contention,
  supports metrics and writes summaries to stderr.
- Empty selections succeed without creating a metadata database.

## Recovery and limits

SPEC §1.2 defines reconcile as the receipt recovery path.
Missing or invalid receipts are rebuilt using the body's modification
time, truncated to seconds. For time layout books whose modification
time falls outside their directory hour, recovery uses that hour's start.
Valid receipts are preserved; receipts for absent books are removed.

Recovered timestamps follow this fallback rule and do not necessarily
recover the original ingestion instant. Metadata itself never performs
this recovery.

Receipts are excluded from E5 storage measurements, as specified in §1.2.

Artifacts, receipts, control files and SQLite are not one transaction.
An interrupted write may require reconcile before metadata can run.
Rerunning metadata repairs a missing book meta.json.

Receipts validate identity, paths and ingestion time; they do not attest
that artifact bytes have remained unchanged after ingestion.

## Validation

Windows validation after merging origin/main:

- Metadata parser/store and pipeline tests: 32 passed.
- Complete Python suite: 240 passed, 18 skipped.
- Branch diff whitespace check: passed.

Skipped tests are not counted as validated. This result does not establish
metadata conformance across Python, Node and Go.