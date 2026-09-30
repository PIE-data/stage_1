import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MetadataStore } from "./metadata_store.js";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "metadata-store-"));
  const store = new MetadataStore(workspace);

  t.after(() => {
    store.close();
    rmSync(workspace, { recursive: true, force: true });
  });

  return { workspace, store };
}

function record(bookId, overrides = {}) {
  return {
    book_id: bookId,
    title: "Café stories",
    author: "Example Author",
    language: "en",
    release_date: null,
    header_path: `datalake/books/${bookId}/header.txt`,
    body_path: `datalake/books/${bookId}/body.txt`,
    body_bytes: 4,
    sha256: "a".repeat(64),
    ingested_at: "2026-09-17T14:03:11Z",
    ...overrides,
  };
}

test("store configures the required pragmas and indexes", (t) => {
  const { store } = fixture(t);

  assert.equal(
    store.db.prepare("PRAGMA journal_mode").get().journal_mode,
    "wal",
  );
  assert.equal(
    store.db.prepare("PRAGMA synchronous").get().synchronous,
    1,
  );
  assert.equal(
    store.db.prepare("PRAGMA cache_size").get().cache_size,
    -262144,
  );

  const indexes = store.db
    .prepare("PRAGMA index_list('books')")
    .all()
    .map((row) => row.name)
    .sort();

  assert.deepEqual(indexes, [
    "idx_books_author",
    "idx_books_language",
    "idx_books_title",
  ]);
});

test("records persist and absent IDs return null", (t) => {
  const { workspace, store } = fixture(t);
  const item = record(42);

  assert.equal(store.writeMany([item]), 1);

  const other = new MetadataStore(workspace);
  try {
    assert.deepEqual(other.byId(42), item);
    assert.equal(other.byId(99), null);
    assert.equal(other.bodyPath(99), null);
  } finally {
    other.close();
  }
});

test("identical records cause zero row writes; changed records replace", (t) => {
  const { store } = fixture(t);
  const item = record(42);

  store.writeMany([item]);

  const before = store.db
    .prepare("SELECT total_changes() AS n")
    .get().n;

  assert.equal(store.writeMany([item]), 0);
  assert.equal(
    store.db.prepare("SELECT total_changes() AS n").get().n,
    before,
  );

  const changed = { ...item, title: "New title" };

  assert.equal(store.writeMany([changed]), 1);
  assert.deepEqual(store.byId(42), changed);
  assert.equal(
    store.db.prepare("SELECT COUNT(*) AS n FROM books").get().n,
    1,
  );
});

test("Q1 to Q4 use the specified query semantics", (t) => {
  const { store } = fixture(t);

  const first = record(1, { title: "Alpha", language: "en" });
  const second = record(2, {
    title: "Alpine",
    author: "Other",
    language: "it",
  });
  const third = record(3, { title: "Beta", language: null });

  store.writeMany([first, second, third]);

  assert.deepEqual(
    store.byAuthor("Example Author").map((r) => r.book_id).sort(),
    [1, 3],
  );

  assert.equal(store.bodyPath(2), second.body_path);

  assert.deepEqual(
    store.byTitlePrefix("Al").map((r) => r.book_id).sort(),
    [1, 2],
  );

  // Preserve SQL LIKE semantics, including wildcard parameters.
  assert.equal(store.byTitlePrefix("%").length, 3);

  assert.deepEqual(
    new Map(store.countByLanguage().map((r) => [r.language, r.count])),
    new Map([[null, 1], ["en", 1], ["it", 1]]),
  );

  assert.deepEqual(store.byAuthor("' OR 1=1 --"), []);
});

test("failure rolls back the entire current batch including replacements", (t) => {
  const { store } = fixture(t);
  const original = record(1);

  store.writeMany([original]);

  assert.throws(() => store.writeMany([
    record(1, { title: "Changed" }),
    record(2),
    record(3, { title: null }),
  ]));

  assert.deepEqual(store.byId(1), original);
  assert.equal(store.byId(2), null);
  assert.equal(store.byId(3), null);

  // The connection remains usable after rollback.
  assert.equal(store.writeMany([record(4)]), 1);
});

test("completed batches survive a later batch failure", (t) => {
  const { store } = fixture(t);

  assert.throws(() => store.writeMany([
    record(1),
    record(2),
    record(3),
    record(4, { title: null }),
  ], { batchSize: 2 }));

  assert.ok(store.byId(1));
  assert.ok(store.byId(2));
  assert.equal(store.byId(3), null);
  assert.equal(store.byId(4), null);
});

test("empty input writes nothing and invalid batch sizes are rejected", (t) => {
  const { store } = fixture(t);

  assert.equal(store.writeMany([]), 0);

  for (const batchSize of [0, -1, 1.5, NaN]) {
    assert.throws(
      () => store.writeMany([], { batchSize }),
      RangeError,
    );
  }
});