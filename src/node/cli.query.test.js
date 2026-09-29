import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { HashStorage } from "./datalake/hash_storage.js";
import { ControlFiles } from "./control/files.js";
import { indexBooks } from "./index_pipeline.js";

const cli = fileURLToPath(new URL("./cli.js", import.meta.url));

function run(root, backend, args) {
  return spawnSync(process.execPath, [
    cli,
    "--workspace", root,
    "--index-backend", backend,
    "query",
    ...args,
  ], { timeout: 15_000 });
}

function check(result, status, stdout) {
  assert.equal(result.status, status, result.stderr?.toString("utf8"));
  assert.deepEqual(result.stdout, Buffer.from(stdout, "utf8"));
}

for (const backend of ["json", "folder", "sqlite"]) {
  test(`CLI query emits exact sorted LF bytes for ${backend}`, async (t) => {
    const root = mkdtempSync(join(tmpdir(), "node-cli-query-"));
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

    check(run(root, backend, [
      "--terms", "CAFÉ river", "--mode", "and",
    ]), 0, "20\n");

    check(run(root, backend, [
      "--terms", "cafe river", "--mode", "or",
    ]), 0, "3\n9\n20\n");

    check(run(root, backend, [
      "--terms", "cafe river", "--mode", "or", "--limit", "2",
    ]), 0, "3\n9\n");

    check(run(root, backend, [
      "--terms", "river", "--mode", "and", "--limit", "0",
    ]), 0, "");

    check(run(root, backend, [
      "--terms", "the 123 a", "--mode", "and",
    ]), 0, "");

    check(run(root, backend, [
      "--terms", "river nonexistent", "--mode", "and",
    ]), 0, "");

    check(run(root, backend, [
      "--terms", "river RIVER the 123", "--mode", "and",
    ]), 0, "3\n20\n");
  });
}

test("CLI query reports invalid arguments and missing indexes", (t) => {
  const root = mkdtempSync(join(tmpdir(), "node-cli-query-errors-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  for (const args of [
    ["--mode", "and"],
    ["--terms", "river"],
    ["--terms", "river", "--mode", "xor"],
    ["--terms", "river", "--mode", "and", "--limit=-1"],
    ["--terms", "river", "--mode", "and", "--limit", "1.5"],
  ]) {
    check(run(root, "json", args), 2, "");
  }

  for (const backend of ["json", "folder", "sqlite"]) {
    check(run(root, backend, [
      "--terms", "river", "--mode", "and",
    ]), 1, "");
  }
});