import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { HashStorage } from "./datalake/hash_storage.js";
import { ControlFiles } from "./control/files.js";
import { indexBooks } from "./index_pipeline.js";
import { queryIndex } from "./query.js";

for (const backend of ["json", "folder", "sqlite"]) {
  test(`query semantics for ${backend}`, async (t) => {
    const root = mkdtempSync(join(tmpdir(), "node-query-"));
    t.after(() => rmSync(root, { recursive: true, force: true }));

    const storage = new HashStorage(root);
    const control = new ControlFiles(root);

    for (const [id, body] of [
      [20, "café river\n"],
      [3, "river mountain\n"],
      [9, "café mountain\n"],
    ]) {
      storage.write(id, "Title: Query fixture\n", body);
      control.markDownloaded(id);
    }

    await indexBooks({
      workspace: root,
      layout: "hash",
      backend,
      all: true,
      positions: true,
    });

    const query = (terms, mode = "and", limit) =>
      queryIndex({ workspace: root, backend, terms, mode, limit });

    assert.deepEqual(await query("CAFÉ river"), [20]);
    assert.deepEqual(await query("cafe river", "or"), [3, 9, 20]);
    assert.deepEqual(await query("river RIVER"), [3, 20]);

    // Filtered terms are ignored; surviving absent terms are not.
    assert.deepEqual(await query("the 123 a river"), [3, 20]);
    assert.deepEqual(await query("river nonexistent"), []);
    assert.deepEqual(await query("river nonexistent", "or"), [3, 20]);

    assert.deepEqual(await query("the 123 a"), []);
    assert.deepEqual(await query("the 123 a", "or"), []);
    assert.deepEqual(await query(""), []);

    assert.deepEqual(await query("cafe river", "or", 2), [3, 9]);
    assert.deepEqual(await query("river", "and", 0), []);

    await assert.rejects(query("river", "xor"), {
      name: "IndexArgumentError",
    });
    await assert.rejects(query("river", "and", -1), {
      name: "IndexArgumentError",
    });
  });

  test(`a missing ${backend} index is an error even for an empty query`, async (t) => {
    const root = mkdtempSync(join(tmpdir(), "node-query-missing-"));
    t.after(() => rmSync(root, { recursive: true, force: true }));

    await assert.rejects(queryIndex({
      workspace: root,
      backend,
      terms: "",
      mode: "and",
      limit: 0,
    }), /No index/u);
  });
}