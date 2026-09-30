import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { HashStorage } from "./datalake/hash_storage.js";
import { ControlFiles } from "./control/files.js";

const cli = fileURLToPath(new URL("./cli.js", import.meta.url));

function run(root, backend, ...args) {
  return spawnSync(process.execPath, [
    cli,
    "--workspace", root,
    "--datalake-layout", "hash",
    "--index-backend", backend,
    ...args,
  ], { encoding: "utf8", timeout: 15_000 });
}

for (const backend of ["json", "folder", "sqlite"]) {
  test(`CLI indexes and exports ${backend} with persisted positions`, (t) => {
    const root = mkdtempSync(join(tmpdir(), "node-cli-index-"));
    t.after(() => rmSync(root, { recursive: true, force: true }));

    new HashStorage(root).write(42, "Title: Test\n", "river river\n");
    const control = new ControlFiles(root);
    control.markDownloaded(42);

    const indexed = run(root, backend, "index", "--all", "--positions");
    assert.equal(indexed.status, 0, indexed.stderr);
    assert.equal(indexed.stdout, "");
    assert.deepEqual([...control.indexedIds()], [42]);

    const output = join(root, "canonical.json");
    const exported = run(root, backend, "export-canonical", "--out", output);
    assert.equal(exported.status, 0, exported.stderr);
    assert.equal(exported.stdout, "");
    assert.equal(
      readFileSync(output, "utf8"),
      '{"river":{"df":1,"postings":[[42,2,[0,1]]]}}',
    );

    const repeated = run(root, backend, "index", "--all", "--positions");
    assert.equal(repeated.status, 0, repeated.stderr);
    assert.match(repeated.stderr, /processed 0/u);

    const mismatch = run(root, backend, "index", "--all");
    assert.equal(mismatch.status, 2, mismatch.stderr);
  });
}

test("CLI rejects invalid index selectors and missing export output", (t) => {
  const root = mkdtempSync(join(tmpdir(), "node-cli-index-args-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  for (const args of [
    ["index"],
    ["index", "--all", "--book-id", "42"],
    ["index", "--all", "--batch-size", "0"],
    ["export-canonical"],
  ]) {
    const result = run(root, "json", ...args);
    assert.equal(result.status, 2, result.stderr);
  }

  const missing = run(root, "json", "index", "--book-id", "42");
  assert.equal(missing.status, 3, missing.stderr);
});