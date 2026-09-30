import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { buildMetadataRecord } from "./metadata_record.js";

function fixture(t, body = Buffer.from("abc", "utf8")) {
  const root = mkdtempSync(join(tmpdir(), "stage1-record-"));
  const workspace = join(root, "workspace");
  const directory = join(workspace, "datalake", "books", "42");

  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, "header.txt"),
    "Title: Example\nAuthor: Doe, Jane, 1900-1980\nLanguage: English\n",
    "utf8",
  );
  writeFileSync(join(directory, "body.txt"), body);

  t.after(() => rmSync(root, { recursive: true, force: true }));

  return {
    root,
    options: {
      bookId: 42,
      workspace,
      headerPath: "datalake/books/42/header.txt",
      bodyPath: "datalake/books/42/body.txt",
      ingestedAt: new Date("2026-01-01T09:30:00+02:00"),
    },
  };
}

test("record contains portable paths, exact hash and UTC ingestion time", (t) => {
  const { options } = fixture(t);

  assert.deepEqual(buildMetadataRecord(options), {
    book_id: 42,
    title: "Example",
    author: "Doe, Jane",
    language: "en",
    release_date: null,
    header_path: "datalake/books/42/header.txt",
    body_path: "datalake/books/42/body.txt",
    body_bytes: 3,
    sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ingested_at: "2026-01-01T07:30:00Z",
  });
});

test("body size counts UTF-8 bytes and preserves stored CRLF", (t) => {
  const { options } = fixture(t, Buffer.from("café\r\n", "utf8"));

  const first = buildMetadataRecord(options);
  assert.equal(first.body_bytes, 7);

  writeFileSync(
    join(options.workspace, options.bodyPath),
    "café\n",
    "utf8",
  );

  const second = buildMetadataRecord(options);
  assert.equal(second.body_bytes, 6);
  assert.notEqual(first.sha256, second.sha256);
});

test("identical inputs produce identical metadata records", (t) => {
  const { options } = fixture(t);

  assert.deepEqual(
    buildMetadataRecord(options),
    buildMetadataRecord(options),
  );
});

test("ingestion time must be supplied explicitly", (t) => {
  const { options } = fixture(t);

  assert.throws(
    () => buildMetadataRecord({ ...options, ingestedAt: undefined }),
    TypeError,
  );
  assert.throws(
    () => buildMetadataRecord({ ...options, ingestedAt: new Date("invalid") }),
    TypeError,
  );
});

test("files outside the workspace are rejected", (t) => {
  const { root, options } = fixture(t);
  const outside = join(root, "outside.txt");

  writeFileSync(outside, "Outside\n", "utf8");

  assert.throws(
    () => buildMetadataRecord({ ...options, bodyPath: outside }),
    /inside the workspace/u,
  );
});