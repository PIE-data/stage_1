import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { SqliteIndex } from "./sqlite_index.js";

function fixture(t, positions = true) {
  const root = mkdtempSync(join(tmpdir(), "node-sqlite-index-"));
  const index = new SqliteIndex(root, { positions });

  t.after(() => {
    index.close();
    rmSync(root, { recursive: true, force: true });
  });

  return { root, index };
}

const tokens = (...terms) =>
  terms.map((term, position) => ({ term, position }));

test("SQLite uses the required schema, pragmas and temporary tracking table", (t) => {
  const { index } = fixture(t);

  assert.equal(index.db.prepare("PRAGMA journal_mode").get().journal_mode, "wal");
  assert.equal(index.db.prepare("PRAGMA synchronous").get().synchronous, 1);
  assert.equal(index.db.prepare("PRAGMA cache_size").get().cache_size, -262144);

  const table = index.db.prepare(`
    SELECT sql FROM sqlite_master
    WHERE type = 'table' AND name = 'postings'
  `).get();

  assert.match(table.sql, /WITHOUT ROWID/u);

  assert.ok(index.db.prepare(`
    SELECT 1 FROM sqlite_master WHERE name = 'idx_postings_book'
  `).get());

  assert.equal(index.db.prepare(`
    SELECT 1 FROM sqlite_master WHERE name = 'touched_terms'
  `).get(), undefined);

  assert.ok(index.db.prepare(`
    SELECT 1 FROM sqlite_temp_master WHERE name = 'touched_terms'
  `).get());
});

test("batch updates preserve positions, sort IDs and compute document frequency", (t) => {
  const { index } = fixture(t);

  index.writeBatch([
    { bookId: 20, tokens: tokens("river", "blue", "river") },
    { bookId: 3, tokens: tokens("river") },
  ]);

  assert.deepEqual(index.postings("river"), [
    [3, 1, [0]],
    [20, 2, [0, 2]],
  ]);

  assert.equal(
    index.db.prepare("SELECT df FROM terms WHERE term = ?").get("river").df,
    2,
  );
  assert.deepEqual(index.postings("absent"), []);
});

test("replacement removes obsolete terms and repeating input preserves results", (t) => {
  const { index } = fixture(t);

  index.writeBook(1, tokens("old", "shared"));
  index.writeBook(2, tokens("shared"));
  index.writeBook(1, tokens("new", "new"));
  index.writeBook(1, tokens("new", "new"));

  assert.deepEqual(index.postings("old"), []);
  assert.deepEqual(index.postings("shared"), [[2, 1, [0]]]);
  assert.deepEqual(index.postings("new"), [[1, 2, [0, 1]]]);

  assert.equal(
    index.db.prepare("SELECT df FROM terms WHERE term = ?").get("new").df,
    1,
  );
  assert.equal(
    index.db.prepare("SELECT df FROM terms WHERE term = ?").get("old"),
    undefined,
  );
});

test("a failed batch rolls back replacements and new postings", (t) => {
  const { index } = fixture(t);
  index.writeBook(1, tokens("original"));

  assert.throws(() => index.writeBatch([
    { bookId: 1, tokens: tokens("replacement") },
    { bookId: 2, tokens: tokens("new") },
    { bookId: 0, tokens: [] },
  ]), /bookId/u);

  assert.deepEqual(index.postings("original"), [[1, 1, [0]]]);
  assert.deepEqual(index.postings("replacement"), []);
  assert.deepEqual(index.postings("new"), []);

  // A subsequent transaction must still work.
  index.writeBook(3, tokens("later"));
  assert.deepEqual(index.postings("later"), [[3, 1, [0]]]);
});

test("completed batches survive failure in a later batch", (t) => {
  const { index } = fixture(t);

  assert.throws(() => index.writeMany([
    { bookId: 1, tokens: tokens("first") },
    { bookId: 2, tokens: tokens("second") },
    { bookId: 3, tokens: tokens("third") },
    { bookId: 0, tokens: [] },
  ], { batchSize: 2 }), /bookId/u);

  assert.deepEqual(index.postings("first"), [[1, 1, [0]]]);
  assert.deepEqual(index.postings("second"), [[2, 1, [0]]]);
  assert.deepEqual(index.postings("third"), []);
});

test("positions-off stores SQL NULL and exports posting pairs", (t) => {
  const { root, index } = fixture(t, false);
  index.writeBook(7, tokens("river", "river"));

  assert.equal(
    index.db.prepare("SELECT positions FROM postings").get().positions,
    null,
  );

  const output = join(root, "canonical.json");
  index.exportCanonical(output);

  assert.equal(
    readFileSync(output, "utf8"),
    '{"river":{"df":1,"postings":[[7,2]]}}',
  );
});

test("records persist across connections and incompatible positions are rejected", (t) => {
  const { root, index } = fixture(t);
  index.writeBook(42, tokens("persistent"));

  const reopened = new SqliteIndex(root, { positions: true });

  try {
    assert.deepEqual(reopened.postings("persistent"), [[42, 1, [0]]]);
  } finally {
    reopened.close();
  }

  assert.throws(
    () => new SqliteIndex(root, { positions: false }),
    /positions/u,
  );
});

test("invalid batch sizes are rejected", (t) => {
  const { index } = fixture(t);

  for (const batchSize of [0, -1, 1.5, NaN]) {
    assert.throws(
      () => index.writeMany([], { batchSize }),
      /batchSize/u,
    );
  }
});