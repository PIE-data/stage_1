import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ControlFiles } from "./control/files.js";
import { HashStorage } from "./datalake/hash_storage.js";
import { indexBooks, openIndex } from "./index_pipeline.js";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "node-index-pipeline-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  return {
    root,
    storage: new HashStorage(root),
    control: new ControlFiles(root),
  };
}

for (const backend of ["json", "folder", "sqlite"]) {
  test(`index --all processes pending books and skips repetition for ${backend}`, async (t) => {
    const { root, storage, control } = fixture(t);

    storage.write(9, "Title: Nine\n", "river river\n");
    storage.write(2, "Title: Two\n", "river mountain\n");
    control.markDownloaded(9);
    control.markDownloaded(2);

    const options = {
      workspace: root,
      layout: "hash",
      backend,
      all: true,
      positions: true,
      batchSize: 2,
    };

    assert.deepEqual(await indexBooks(options), {
      exitCode: 0,
      processed: 2,
    });
    assert.deepEqual([...control.indexedIds()], [2, 9]);

    const index = await openIndex(root, backend, true);

    try {
      assert.deepEqual(index.postings("river"), [
        [2, 1, [0]],
        [9, 2, [0, 1]],
      ]);
    } finally {
      index.close?.();
    }

    assert.deepEqual(await indexBooks(options), {
      exitCode: 0,
      processed: 0,
    });

    await assert.rejects(
      indexBooks({ ...options, positions: false }),
      { name: "IndexArgumentError" },
    );
  });
}

test("a missing book prevents the current batch from being marked indexed", async (t) => {
  const { root, storage, control } = fixture(t);

  storage.write(1, "Title: One\n", "river\n");
  control.markDownloaded(1);
  control.markDownloaded(2);

  assert.deepEqual(await indexBooks({
    workspace: root,
    layout: "hash",
    all: true,
    batchSize: 2,
  }), { exitCode: 3, processed: 0 });

  assert.equal(control.indexedIds().size, 0);
  assert.equal(existsSync(join(root, "datamarts")), false);
});

test("an empty selection does not create an index", async (t) => {
  const { root } = fixture(t);

  assert.deepEqual(await indexBooks({
    workspace: root,
    layout: "hash",
    all: true,
  }), { exitCode: 0, processed: 0 });

  assert.equal(existsSync(join(root, "datamarts")), false);
});

test("positions remain fixed even when a book produces no terms", async (t) => {
  const { root, storage, control } = fixture(t);

  storage.write(1, "Title: Empty\n", "12345\n");
  control.markDownloaded(1);

  await indexBooks({
    workspace: root,
    layout: "hash",
    all: true,
    positions: true,
  });

  await assert.rejects(indexBooks({
    workspace: root,
    layout: "hash",
    all: true,
    positions: false,
  }), { name: "IndexArgumentError" });
});