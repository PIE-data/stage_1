import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  unlinkSync,
  utimesSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { TimeStorage } from "./time_storage.js";

function workspaceFor(t) {
  const workspace = mkdtempSync(join(tmpdir(), "stage1-time-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

function at(workspace, timestamp) {
  return new TimeStorage(workspace, { now: new Date(timestamp) });
}

test("time layout uses UTC even when the override has an offset", (t) => {
  const workspace = workspaceFor(t);
  const storage = at(workspace, "2026-01-01T01:30:00+02:00");

  assert.deepEqual(storage.write(1342, "Header\n", "Body\n"), [
    "datalake/20251231/23/1342.header.txt",
    "datalake/20251231/23/1342.body.txt",
  ]);
});

test("write and lookup preserve exact UTF-8 bytes", (t) => {
  const workspace = workspaceFor(t);
  const storage = at(workspace, "2026-01-01T07:30:00Z");
  const header = "Title: Café\n";
  const body = "First\n  Second\n";

  const paths = storage.write(1342, header, body);

  // A new instance must discover the files without an ingestion-time cache.
  assert.deepEqual(new TimeStorage(workspace).lookup(1342), paths);
  assert.deepEqual(
    readFileSync(join(workspace, paths[0])),
    Buffer.from(header, "utf8"),
  );
  assert.deepEqual(
    readFileSync(join(workspace, paths[1])),
    Buffer.from(body, "utf8"),
  );
});

test("lookup returns null for absent or incomplete books", (t) => {
  const workspace = workspaceFor(t);
  const storage = at(workspace, "2026-01-01T07:00:00Z");

  assert.equal(storage.lookup(42), null);

  const paths = storage.write(42, "Header\n", "Body\n");
  unlinkSync(join(workspace, paths[0]));

  assert.equal(storage.lookup(42), null);
});

test("lookup discovers newly added hour directories without caching", (t) => {
  const workspace = workspaceFor(t);
  const reader = new TimeStorage(workspace);

  assert.equal(reader.lookup(42), null);

  const paths = at(workspace, "2026-01-02T15:00:00Z")
    .write(42, "Header\n", "Body\n");

  assert.deepEqual(reader.lookup(42), paths);

  unlinkSync(join(workspace, paths[1]));
  assert.equal(reader.lookup(42), null);
});

test("listNew includes the boundary hour and later dates", (t) => {
  const workspace = workspaceFor(t);

  at(workspace, "2025-12-31T23:00:00Z").write(1, "H\n", "B\n");
  at(workspace, "2026-01-01T06:00:00Z").write(2, "H\n", "B\n");
  at(workspace, "2026-01-01T07:00:00Z").write(3, "H\n", "B\n");
  at(workspace, "2026-01-01T08:00:00Z").write(4, "H\n", "B\n");
  at(workspace, "2026-01-02T00:00:00Z").write(5, "H\n", "B\n");

  const storage = new TimeStorage(workspace);

  // Minutes do not exclude books within the boundary hour.
  assert.deepEqual(
    storage.listNew(new Date("2026-01-01T07:45:00Z")),
    [3, 4, 5],
  );
});

test("listNew uses directory timestamps rather than body mtime", (t) => {
  const workspace = workspaceFor(t);
  const storage = at(workspace, "2026-01-01T07:00:00Z");
  const paths = storage.write(42, "Header\n", "Body\n");
  const old = new Date("2000-01-01T00:00:00Z");

  utimesSync(join(workspace, paths[1]), old, old);

  assert.deepEqual(
    storage.listNew(new Date("2026-01-01T07:00:00Z")),
    [42],
  );
});

test("an absent datalake yields no new books", (t) => {
  const workspace = workspaceFor(t);
  const storage = new TimeStorage(workspace);

  assert.deepEqual(storage.listNew(new Date(0)), []);
});

test("invalid dates and book IDs are rejected", (t) => {
  const workspace = workspaceFor(t);

  assert.throws(
    () => new TimeStorage(workspace, { now: new Date("invalid") }),
    TypeError,
  );

  const storage = new TimeStorage(workspace);

  assert.throws(() => storage.lookup(-1), TypeError);
  assert.throws(() => storage.listNew(new Date("invalid")), TypeError);
});