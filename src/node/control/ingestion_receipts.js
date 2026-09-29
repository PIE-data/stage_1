import { readFileSync } from "node:fs";
import { posix, resolve } from "node:path";
import { atomicWrite } from "../datalake/atomic.js";

function validateIdentity(layout, bookId) {
  if (!["book", "hash", "time"].includes(layout)) {
    throw new RangeError(`Unknown datalake layout: ${layout}`);
  }

  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new RangeError("bookId must be a positive safe integer");
  }
}

function utcTimestamp(instant) {
  if (!(instant instanceof Date) || !Number.isFinite(instant.getTime())) {
    throw new TypeError("A valid ingestion instant must be supplied");
  }

  if (instant.getUTCFullYear() < 0 || instant.getUTCFullYear() > 9999) {
    throw new RangeError("Ingestion year must be between 0000 and 9999");
  }

  return instant.toISOString().replace(".000Z", "Z");
}

function expectedPaths(layout, bookId, instant) {
  if (layout === "book") {
    const root = `datalake/books/${bookId}`;
    return [`${root}/header.txt`, `${root}/body.txt`];
  }

  let root;

  if (layout === "hash") {
    const id6 = String(bookId).padStart(6, "0");
    root = `datalake/${id6.slice(0, 2)}/${id6.slice(2, 4)}`;
  } else {
    const stamp = instant.toISOString();
    const date = stamp.slice(0, 10).replaceAll("-", "");
    root = `datalake/${date}/${stamp.slice(11, 13)}`;
  }

  return [
    `${root}/${bookId}.header.txt`,
    `${root}/${bookId}.body.txt`,
  ];
}

function validatePaths(paths, expected) {
  if (!Array.isArray(paths) || paths.length !== 2) {
    throw new Error("Receipt requires header and body paths");
  }

  for (let i = 0; i < paths.length; i += 1) {
    const path = paths[i];

    if (
      typeof path !== "string" ||
      path.includes("\\") ||
      posix.isAbsolute(path) ||
      path !== expected[i]
    ) {
      throw new Error("Receipt paths do not match the selected layout");
    }
  }
}

function receiptPath(workspace, layout, bookId) {
  return resolve(
    workspace,
    "control",
    "ingestion",
    layout,
    `${bookId}.json`,
  );
}

export function writeIngestionReceipt({
  workspace,
  layout,
  bookId,
  paths,
  ingestedAt,
}) {
  validateIdentity(layout, bookId);
  const timestamp = utcTimestamp(ingestedAt);
  validatePaths(paths, expectedPaths(layout, bookId, ingestedAt));

  const record = {
    book_id: bookId,
    header_path: paths[0],
    body_path: paths[1],
    ingested_at: timestamp,
  };

  atomicWrite(
    receiptPath(workspace, layout, bookId),
    `${JSON.stringify(record)}\n`,
  );

  return record;
}

export function readIngestionReceipt({
  workspace,
  layout,
  bookId,
  paths,
}) {
  validateIdentity(layout, bookId);

  let record;

  try {
    record = JSON.parse(readFileSync(
      receiptPath(workspace, layout, bookId),
      "utf8",
    ));
  } catch (error) {
    throw new Error(
      `Cannot read ingestion receipt for book ${bookId}: ${error.message}`,
      { cause: error },
    );
  }

  if (
    !record ||
    typeof record !== "object" ||
    record.book_id !== bookId ||
    typeof record.ingested_at !== "string"
  ) {
    throw new Error(`Malformed ingestion receipt for book ${bookId}`);
  }

  const instant = new Date(record.ingested_at);

  if (
    !Number.isFinite(instant.getTime()) ||
    utcTimestamp(instant) !== record.ingested_at
  ) {
    throw new Error(`Invalid receipt timestamp for book ${bookId}`);
  }

  const expected = expectedPaths(layout, bookId, instant);

  validatePaths([record.header_path, record.body_path], expected);
  validatePaths(paths, expected);

  return record;
}