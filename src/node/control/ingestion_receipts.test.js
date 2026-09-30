import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import {
  readIngestionReceipt,
  writeIngestionReceipt,
} from "./ingestion_receipts.js";

const STAMP = "2026-09-17T14:03:11Z";

function fixture(t, layout = "hash") {
  const workspace = mkdtempSync(join(tmpdir(), "receipts-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  const paths = {
    book: [
      "datalake/books/42/header.txt",
      "datalake/books/42/body.txt",
    ],
    hash: [
      "datalake/00/00/42.header.txt",
      "datalake/00/00/42.body.txt",
    ],
    time: [
      "datalake/20260917/14/42.header.txt",
      "datalake/20260917/14/42.body.txt",
    ],
  }[layout];

  return {
    workspace,
    layout,
    bookId: 42,
    paths,
    ingestedAt: new Date(STAMP),
    receiptPath: join(
      workspace, "control", "ingestion", layout, "42.json",
    ),
  };
}

for (const layout of ["book", "hash", "time"]) {
  test(`receipt roundtrip preserves paths and timestamp for ${layout}`, (t) => {
    const options = fixture(t, layout);
    const written = writeIngestionReceipt(options);

    assert.deepEqual(readIngestionReceipt(options), written);
    assert.equal(written.ingested_at, STAMP);

    const text = readFileSync(options.receiptPath, "utf8");
    assert.equal(text, `${JSON.stringify(written)}\n`);
    assert.equal(existsSync(`${options.receiptPath}.part`), false);
  });
}

test("a missing receipt is rejected without creating files", (t) => {
  const options = fixture(t);

  assert.throws(
    () => readIngestionReceipt(options),
    /Cannot read ingestion receipt/,
  );

  assert.equal(existsSync(join(options.workspace, "control")), false);
});

test("malformed JSON and invalid record fields are rejected", (t) => {
  const options = fixture(t);
  const valid = writeIngestionReceipt(options);

  const invalidRecords = [
    "{broken",
    "null",
    "[]",
    JSON.stringify({ ...valid, book_id: 43 }),
    JSON.stringify({ ...valid, ingested_at: "not-a-date" }),
    JSON.stringify({ ...valid, ingested_at: "2026-02-30T14:03:11Z" }),
    JSON.stringify({ ...valid, ingested_at: null }),
  ];

  for (const text of invalidRecords) {
    writeFileSync(options.receiptPath, text, "utf8");
    assert.throws(() => readIngestionReceipt(options));
  }
});

test("receipt paths must match both the layout and the resolved artifacts", (t) => {
  const options = fixture(t);
  const valid = writeIngestionReceipt(options);

  for (const bodyPath of [
    "../outside.txt",
    "/absolute/body.txt",
    "datalake\\00\\00\\42.body.txt",
    "datalake/00/01/42.body.txt",
    "datalake/00/00/43.body.txt",
  ]) {
    writeFileSync(
      options.receiptPath,
      JSON.stringify({ ...valid, body_path: bodyPath }),
      "utf8",
    );

    assert.throws(
      () => readIngestionReceipt(options),
      /Receipt paths/,
    );
  }

  writeFileSync(options.receiptPath, JSON.stringify(valid), "utf8");

  assert.throws(
    () => readIngestionReceipt({
      ...options,
      paths: [options.paths[0], "datalake/00/00/43.body.txt"],
    }),
    /Receipt paths/,
  );
});

test("a time receipt rejects a timestamp inconsistent with its directory", (t) => {
  const options = fixture(t, "time");
  const valid = writeIngestionReceipt(options);

  writeFileSync(
    options.receiptPath,
    JSON.stringify({
      ...valid,
      ingested_at: "2026-09-17T15:03:11Z",
    }),
    "utf8",
  );

  assert.throws(
    () => readIngestionReceipt(options),
    /Receipt paths/,
  );
});

test("invalid write arguments create no receipt", (t) => {
  const options = fixture(t);

  assert.throws(() => writeIngestionReceipt({
    ...options,
    bookId: 0,
  }));

  assert.throws(() => writeIngestionReceipt({
    ...options,
    ingestedAt: new Date("invalid"),
  }));

  assert.throws(() => writeIngestionReceipt({
    ...options,
    paths: ["../header.txt", "../body.txt"],
  }));

  assert.equal(existsSync(join(options.workspace, "control")), false);
});

test("an existing partial receipt is preserved and the write fails", (t) => {
  const options = fixture(t);

  mkdirSync(dirname(options.receiptPath), { recursive: true });
  writeFileSync(`${options.receiptPath}.part`, "unfinished", "utf8");

  assert.throws(() => writeIngestionReceipt(options));

  assert.equal(existsSync(options.receiptPath), false);
  assert.equal(
    readFileSync(`${options.receiptPath}.part`, "utf8"),
    "unfinished",
  );
});