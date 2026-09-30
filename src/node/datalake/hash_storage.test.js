import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  unlinkSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { HashStorage } from "./hash_storage.js";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "stage1-hash-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return {
    workspace,
    storage: new HashStorage(workspace),
  };
}

test("hash layout uses zero-padded decimal digit prefixes", (t) => {
  const { storage } = fixture(t);

  assert.deepEqual(storage.paths(1342), [
    "datalake/00/13/1342.header.txt",
    "datalake/00/13/1342.body.txt",
  ]);

  assert.deepEqual(storage.paths(7), [
    "datalake/00/00/7.header.txt",
    "datalake/00/00/7.body.txt",
  ]);

  assert.deepEqual(storage.paths(123456), [
    "datalake/12/34/123456.header.txt",
    "datalake/12/34/123456.body.txt",
  ]);
});

test("write and lookup preserve exact UTF-8 bytes", (t) => {
  const { workspace, storage } = fixture(t);
  const header = "Title: Café\n";
  const body = "First line\n  Indented line\n";

  const paths = storage.write(1342, header, body);

  assert.deepEqual(storage.lookup(1342), paths);
  assert.deepEqual(
    readFileSync(join(workspace, paths[0])),
    Buffer.from(header, "utf8"),
  );
  assert.deepEqual(
    readFileSync(join(workspace, paths[1])),
    Buffer.from(body, "utf8"),
  );
});

test("lookup returns null for missing or incomplete books", (t) => {
  const { workspace, storage } = fixture(t);

  assert.equal(storage.lookup(42), null);

  const paths = storage.write(42, "Title: Example\n", "Body\n");
  unlinkSync(join(workspace, paths[0]));

  assert.equal(storage.lookup(42), null);
});

test("lookup observes filesystem changes without caching", (t) => {
  const { workspace, storage } = fixture(t);

  const paths = storage.write(42, "Title: Example\n", "Body\n");
  assert.deepEqual(storage.lookup(42), paths);

  unlinkSync(join(workspace, paths[1]));
  assert.equal(storage.lookup(42), null);

  writeFileSync(join(workspace, paths[1]), "Restored\n", "utf8");
  assert.deepEqual(storage.lookup(42), paths);
});

test("listNew filters body mtime inclusively across buckets", (t) => {
  const { workspace, storage } = fixture(t);
  const boundary = new Date("2026-01-01T12:00:00Z");

  const cases = [
    [1342, new Date("2026-01-01T11:00:00Z")],
    [123456, boundary],
    [7, new Date("2026-01-01T13:00:00Z")],
  ];

  for (const [id, timestamp] of cases) {
    const paths = storage.write(id, "Title: Example\n", "Body\n");
    utimesSync(join(workspace, paths[1]), timestamp, timestamp);
  }

  assert.deepEqual(storage.listNew(boundary), [7, 123456]);
});

test("listNew ignores partial files and misplaced bodies", (t) => {
  const { workspace, storage } = fixture(t);

  storage.write(42, "Title: Example\n", "Body\n");

  const bucket = join(workspace, "datalake", "00", "00");
  writeFileSync(join(bucket, "7.body.txt.part"), "Partial\n");

  const wrongBucket = join(workspace, "datalake", "99", "99");
  mkdirSync(wrongBucket, { recursive: true });
  writeFileSync(join(wrongBucket, "7.body.txt"), "Misplaced\n");

  assert.deepEqual(storage.listNew(new Date(0)), [42]);
});

test("an absent datalake yields no new books", (t) => {
  const { storage } = fixture(t);

  assert.deepEqual(storage.listNew(new Date(0)), []);
});

test("invalid IDs and dates are rejected", (t) => {
  const { storage } = fixture(t);

  for (const id of [0, -1, 1.5, "42", Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => storage.lookup(id), TypeError);
  }

  assert.throws(() => storage.listNew(new Date("invalid")), TypeError);
});