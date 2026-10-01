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

import { makeStorage } from "../ingestion.js";
import { acquireRunLock } from "./run_lock.js";
import { reconcileWorkspace } from "./reconcile.js";

const NOW = new Date("2026-09-17T14:03:11Z");

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "reconcile-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

function put(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, text, "utf8");
}

for (const layout of ["book", "hash", "time"]) {
  test(`reconcile repairs control files and removes partials for ${layout}`, async (t) => {
    const workspace = fixture(t);
    const storage = makeStorage(workspace, layout, NOW);

    storage.write(9, "Title: Nine\n", "Nine\n");
    storage.write(2, "Title: Two\n", "Two\n");
    const incomplete = storage.write(7, "Title: Seven\n", "Seven\n");

    rmSync(join(workspace, incomplete[0]));

    const downloadedPath = join(
      workspace, "control", "downloaded_books.txt",
    );
    const indexedPath = join(
      workspace, "control", "indexed_books.txt",
    );

    put(downloadedPath, "99\n9\n9\n7\n");
    put(indexedPath, "99\n9\n9\n7\n");

    const partials = [
      join(workspace, "raw", "12.txt.part"),
      join(workspace, `${incomplete[0]}.part`),
      `${downloadedPath}.part`,
    ];

    for (const path of partials) put(path, "Interrupted write");

    const result = await reconcileWorkspace({ workspace, layout });

    assert.deepEqual(result, {
      downloaded: 2,
      indexed: 1,
      partialsRemoved: 3,
    });

    assert.equal(readFileSync(downloadedPath, "utf8"), "2\n9\n");
    assert.equal(readFileSync(indexedPath, "utf8"), "9\n");

    for (const path of partials) {
      assert.equal(existsSync(path), false);
    }

    // Incomplete final artifacts are retained, but never marked downloaded.
    assert.equal(existsSync(join(workspace, incomplete[1])), true);

    // Storage writes alone did not create pipeline receipts.
    assert.equal(
      existsSync(join(workspace, "control", "ingestion")),
      false,
    );

    assert.deepEqual(
      await reconcileWorkspace({ workspace, layout }),
      { downloaded: 2, indexed: 1, partialsRemoved: 0 },
    );

    assert.equal(readFileSync(downloadedPath, "utf8"), "2\n9\n");
  });
}

test("reconcile creates empty control files for an empty workspace", async (t) => {
  const workspace = fixture(t);

  assert.deepEqual(
    await reconcileWorkspace({ workspace, layout: "hash" }),
    { downloaded: 0, indexed: 0, partialsRemoved: 0 },
  );

  for (const name of ["downloaded_books.txt", "indexed_books.txt"]) {
    assert.equal(
      readFileSync(join(workspace, "control", name), "utf8"),
      "",
    );
  }
});

test("reconcile rejects a locked workspace before deleting partials", async (t) => {
  const workspace = fixture(t);
  const partial = join(workspace, "raw", "42.txt.part");
  put(partial, "Keep this");

  const lock = await acquireRunLock(workspace);

  try {
    await assert.rejects(
      reconcileWorkspace({ workspace, layout: "hash" }),
      (error) => error.name === "WorkspaceLockedError",
    );

    assert.equal(readFileSync(partial, "utf8"), "Keep this");
    assert.equal(
      existsSync(join(workspace, "control", "downloaded_books.txt")),
      false,
    );
  } finally {
    await lock.release();
  }
});