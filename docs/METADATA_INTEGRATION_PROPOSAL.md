# Metadata integration — proposal, not an approved specification change

This implementation targets the uploaded SPEC 1.1.3 snapshot. Do not merge
until the group accepts the observable rules below through its spec-change
process. SPEC.md, SPEC_VERSION and supported-version constants are unchanged.

## Proposed shared behaviour

- Each successful storage write persists a receipt at
  `control/ingestion/<layout>/<book_id>.json` containing workspace-relative
  header/body paths and an ISO8601 UTC ingestion timestamp.
- The timestamp is the storage write instant, overridden by global `--now`
  for all layouts. For `time`, it is also used to choose the directory.
  Offline `split` is a new write/ingestion and receives a new timestamp.
- Header and body are written first. The book layout also writes its complete
  `meta.json`; the receipt is written last, before the pipeline marks the book
  downloaded. The existing atomic-write helper is used for both JSON artifacts.
  SQLite remains the responsibility of the explicit `metadata` command.
- `metadata --all` enumerates the selected layout, sorts and deduplicates IDs;
  `metadata --book-id` selects one book. It reads persisted timestamps only:
  it does not infer them from mtime, the current clock or hour-only paths.
- Missing books exit 3. Missing, mismatching or malformed receipts exit 1.
  All receipt/path validation occurs before metadata writes. Old workspaces
  need an explicit migration decision; no historical timestamp is fabricated.
  A caller may re-split cached raw files as a NEW ingestion, but this does not
  recover the original ingestion timestamp.
- `--batch-size` is accepted for metadata (default 500, positive). Records
  are built and written one batch at a time. Repeated identical records cause
  zero SQLite row writes and do not replace identical book meta.json files.
- Metadata uses the existing workspace writer lock (exit 4 on contention),
  emits metrics when requested, and writes summaries only to stderr.
- Empty selections succeed without creating a metadata database.

## Coordination and limits

The receipt format, timestamp semantics, metadata flags/exit codes and writer
lock must be documented and ported to Node/Go before claiming cross-language
conformance. Clarify whether control-side receipt bytes belong in E5's storage
measurement. There is intentionally no silent SPEC version bump in this draft.

The receipt and SQLite are not one transaction. An interrupted storage write
may leave artifacts without a receipt; metadata rejects that state. Existing
reconcile still repairs the original header/body control invariant, not receipts.
A recovery policy must be agreed before production/benchmark use. An interruption
between SQLite and meta.json can be repaired by rerunning metadata. Receipt paths
are validated against the selected layout, but receipts do not attest that an
external program has not modified artifact bytes since ingestion.

Validation is against a local Linux/Python environment. The user must repeat the
suite on Windows before updating PR #81. Existing layout traversal behaviour is
unchanged; workspaces should contain only their selected layout.
