import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  unlinkSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { BookStorage } from "./book_storage.js";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "stage1-book-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return {
    workspace,
    storage: new BookStorage(workspace, {
      now: new Date("2026-01-01T07:30:00Z"),
    }),
  };
}

test("book layout writes exact bytes and a complete metadata record", (t) => {
  const { workspace, storage } = fixture(t);
  const header = "Title: Café\nLanguage: English\n";

  const paths = storage.write(42, header, "abc");

  assert.deepEqual(paths, [
    "datalake/books/42/header.txt",
    "datalake/books/42/body.txt",
  ]);
  assert.deepEqual(
    readFileSync(join(workspace, paths[0])),
    Buffer.from(header, "utf8"),
  );
  assert.deepEqual(
    readFileSync(join(workspace, paths[1])),
    Buffer.from("abc"),
  );

  const record = JSON.parse(readFileSync(
    join(workspace, "datalake/books/42/meta.json"),
    "utf8",
  ));

  assert.deepEqual(record, {
    book_id: 42,
    title: "Café",
    author: null,
    language: "en",
    release_date: null,
    header_path: paths[0],
    body_path: paths[1],
    body_bytes: 3,
    sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ingested_at: "2026-01-01T07:30:00Z",
  });
});

test("lookup works without consulting meta.json", (t) => {
  const { workspace, storage } = fixture(t);
  const paths = storage.write(42, "Title: Example\n", "Body\n");
  const meta = join(workspace, "datalake/books/42/meta.json");

  writeFileSync(meta, "Invalid JSON", "utf8");
  assert.deepEqual(storage.lookup(42), paths);

  unlinkSync(meta);
  assert.deepEqual(new BookStorage(workspace).lookup(42), paths);
});

test("lookup observes missing body files without caching", (t) => {
  const { workspace, storage } = fixture(t);

  assert.equal(storage.lookup(42), null);

  const paths = storage.write(42, "Title: Example\n", "Body\n");
  assert.deepEqual(storage.lookup(42), paths);

  unlinkSync(join(workspace, paths[1]));
  assert.equal(storage.lookup(42), null);
});

test("listNew filters body mtime inclusively", (t) => {
  const { workspace, storage } = fixture(t);
  const boundary = new Date("2026-01-01T12:00:00Z");

  for (const [id, timestamp] of [
    [1, new Date("2026-01-01T11:00:00Z")],
    [2, boundary],
    [3, new Date("2026-01-01T13:00:00Z")],
  ]) {
    const paths = storage.write(id, "Title: Example\n", "Body\n");
    utimesSync(join(workspace, paths[1]), timestamp, timestamp);
  }

  assert.deepEqual(storage.listNew(boundary), [2, 3]);
});

test("writing requires an explicit ingestion time before creating files", (t) => {
  const { workspace } = fixture(t);
  const storage = new BookStorage(workspace);

  assert.throws(
    () => storage.write(42, "Title: Example\n", "Body\n"),
    /explicit ingestion time/u,
  );
  assert.equal(existsSync(join(workspace, "datalake")), false);
});

test("an absent book layout yields no new books", (t) => {
  const { storage } = fixture(t);

  assert.deepEqual(storage.listNew(new Date(0)), []);
});