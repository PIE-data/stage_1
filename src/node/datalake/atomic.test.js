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
import { join } from "node:path";

import { atomicWrite } from "./atomic.js";

function temporaryDirectory(t) {
  const directory = mkdtempSync(join(tmpdir(), "stage1-atomic-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("atomic write creates directories and preserves UTF-8 and LF bytes", (t) => {
  const root = temporaryDirectory(t);
  const target = join(root, "nested", "body.txt");
  const content = "Café\nSecond line\n";

  const result = atomicWrite(target, content);

  assert.deepEqual(readFileSync(target), Buffer.from(content, "utf8"));
  assert.equal(existsSync(`${target}.part`), false);
  assert.equal(typeof result.directorySynced, "boolean");

  if (process.platform !== "win32") {
    assert.equal(result.directorySynced, true);
  }
});

test("atomic write replaces an existing file completely", (t) => {
  const root = temporaryDirectory(t);
  const target = join(root, "body.txt");

  writeFileSync(target, "A much longer previous document\n", "utf8");
  atomicWrite(target, "New\n");

  assert.equal(readFileSync(target, "utf8"), "New\n");
  assert.equal(existsSync(`${target}.part`), false);
});

test("atomic write preserves arbitrary Buffer bytes", (t) => {
  const root = temporaryDirectory(t);
  const target = join(root, "data.bin");
  const content = Buffer.from([0, 10, 13, 127, 128, 255]);

  atomicWrite(target, content);

  assert.deepEqual(readFileSync(target), content);
});

test("an existing partial file is not overwritten or deleted", (t) => {
  const root = temporaryDirectory(t);
  const target = join(root, "body.txt");

  writeFileSync(target, "Original\n", "utf8");
  writeFileSync(`${target}.part`, "Existing partial\n", "utf8");

  assert.throws(
    () => atomicWrite(target, "Replacement\n"),
    { code: "EEXIST" },
  );

  assert.equal(readFileSync(target, "utf8"), "Original\n");
  assert.equal(readFileSync(`${target}.part`, "utf8"), "Existing partial\n");
});

test("a failed rename removes this writer's partial file", (t) => {
  const root = temporaryDirectory(t);
  const target = join(root, "occupied");

  mkdirSync(target);
  writeFileSync(join(target, "keep.txt"), "Keep\n", "utf8");

  assert.throws(() => atomicWrite(target, "Replacement\n"));

  assert.equal(existsSync(`${target}.part`), false);
  assert.equal(readFileSync(join(target, "keep.txt"), "utf8"), "Keep\n");
});