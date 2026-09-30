import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ControlFiles, readBookIds } from "./files.js";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "control-files-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return {
    workspace,
    control: new ControlFiles(workspace),
  };
}

test("absent control files return empty sets without creating files", (t) => {
  const { control } = fixture(t);

  assert.deepEqual(control.downloadedIds(), new Set());
  assert.deepEqual(control.indexedIds(), new Set());
  assert.equal(existsSync(control.root), false);
});

test("downloaded IDs are appended once with LF endings", (t) => {
  const { control } = fixture(t);

  assert.equal(control.markDownloaded(42), true);
  assert.equal(control.markDownloaded(7), true);
  assert.equal(control.markDownloaded(42), false);

  assert.equal(
    readFileSync(control.downloadedPath, "utf8"),
    "42\n7\n",
  );

  assert.deepEqual(control.downloadedIds(), new Set([42, 7]));
});

test("reading IDs accepts CRLF and removes duplicates", (t) => {
  const { workspace } = fixture(t);
  const path = join(workspace, "ids.txt");

  writeFileSync(path, "42\r\n7\r\n42\r\n\r\n", "utf8");

  assert.deepEqual(readBookIds(path), new Set([42, 7]));
});

test("malformed control files are reported instead of silently ignored", (t) => {
  const { workspace } = fixture(t);
  const path = join(workspace, "ids.txt");

  for (const text of ["0\n", "-1\n", "abc\n", "9007199254740992\n"]) {
    writeFileSync(path, text, "utf8");
    assert.throws(() => readBookIds(path));
  }
});

test("failures append the required reason and UTC timestamp", (t) => {
  const { control } = fixture(t);
  const instant = new Date("2026-09-17T16:03:11+02:00");

  control.recordFailure(42, "NO_MARKERS", instant);
  control.recordFailure(7, "NOT_FOUND", instant);
  control.recordFailure(8, "DOWNLOAD_ERROR", instant);

  assert.equal(
    readFileSync(control.failedPath, "utf8"),
    "42\tNO_MARKERS\t2026-09-17T14:03:11Z\n" +
    "7\tNOT_FOUND\t2026-09-17T14:03:11Z\n" +
    "8\tDOWNLOAD_ERROR\t2026-09-17T14:03:11Z\n",
  );

  assert.deepEqual(control.downloadedIds(), new Set());
});

test("invalid failure arguments do not create control files", (t) => {
  const { control } = fixture(t);
  const instant = new Date("2026-09-17T14:03:11Z");

  assert.throws(() => control.markDownloaded(0), RangeError);
  assert.throws(
    () => control.recordFailure(42, "UNKNOWN", instant),
    RangeError,
  );
  assert.throws(
    () => control.recordFailure(42, "NO_MARKERS", new Date("invalid")),
    TypeError,
  );

  assert.equal(existsSync(control.root), false);
});