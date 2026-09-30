import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { controlStep } from "./control_step.js";
import { ControlFiles } from "./files.js";
import { DownloadError } from "../datalake/downloader.js";
import { readIndexConfig } from "../index_pipeline.js";

const RAW = [
  "Title: Control fixture",
  "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***",
  "river mountain",
  "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***",
].join("\n");

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "node-control-step-"));
  t.after(() => rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

test("control-step advances one stage per iteration and resumes in manifest order", async (t) => {
  const workspace = fixture(t);
  const requests = [];
  const downloaderFactory = () => ({
    async download(id) {
      requests.push(id);
      return RAW;
    },
    async close() {},
  });

  const options = {
    workspace,
    layout: "hash",
    backend: "json",
    bookIds: [9, 2],
    now: new Date("2026-01-01T00:00:00Z"),
    downloaderFactory,
  };

  const first = await controlStep({ ...options, iterations: 1 });
  assert.equal(first.downloaded, 1);
  assert.equal(first.indexed, 0);

  const control = new ControlFiles(workspace);
  assert.deepEqual([...control.downloadedIds()], [9]);
  assert.deepEqual([...control.indexedIds()], []);

  const second = await controlStep({ ...options, iterations: 3 });
  assert.equal(second.downloaded, 1);
  assert.equal(second.indexed, 2);
  assert.deepEqual(requests, [9, 2]);
  assert.deepEqual([...control.indexedIds()], [9, 2]);
  assert.equal(readIndexConfig(workspace, "json").positions, true);

  const exhausted = await controlStep({ ...options, iterations: 10 });
  assert.equal(exhausted.iterations, 0);
  assert.deepEqual(requests, [9, 2]);
});

test("failed candidates are recorded, skipped on resume and do not stop the run", async (t) => {
  const workspace = fixture(t);
  const requests = [];

  const options = {
    workspace,
    layout: "hash",
    backend: "json",
    totalBooks: 2,
    iterations: 10,
    downloaderFactory: () => ({
      async download(id) {
        requests.push(id);
        if (id === 1) throw new DownloadError("Missing", "NOT_FOUND", 404);
        return RAW;
      },
      async close() {},
    }),
  };

  const result = await controlStep(options);
  assert.equal(result.exitCode, 0);
  assert.equal(result.failed, 1);
  assert.equal(result.downloaded, 1);
  assert.equal(result.indexed, 1);
  assert.equal(result.iterations, 3);

  await controlStep(options);
  assert.deepEqual(requests, [1, 2]);
});