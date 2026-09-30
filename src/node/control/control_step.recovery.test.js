import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { controlStep } from "./control_step.js";
import { ControlFiles } from "./files.js";
import { HashStorage } from "../datalake/hash_storage.js";
import { exportIndex } from "../index_pipeline.js";

const CONTROL_URL = new URL("./control_step.js", import.meta.url).href;
const FILES_URL = new URL("./files.js", import.meta.url).href;
const STAMP = "2026-01-01T00:00:00Z";

const RAW = [
  "Title: Recovery fixture",
  "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***",
  "river mountain river",
  "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***",
].join("\n");

function factory() {
  return {
    async download() { return RAW; },
    async close() {},
  };
}

function verifyControlAndArtifacts(workspace, count) {
  const control = new ControlFiles(workspace);
  const expected = Array.from({ length: count }, (_, i) => i + 1);

  for (const path of [control.downloadedPath, control.indexedPath]) {
    const lines = readFileSync(path, "utf8").trim().split("\n").map(Number);
    assert.deepEqual(lines, expected);
    assert.equal(new Set(lines).size, count);
  }

  const storage = new HashStorage(workspace);

  for (const id of expected) {
    const paths = storage.lookup(id);
    assert.notEqual(paths, null);
    assert.equal(
      readFileSync(join(workspace, paths[0]), "utf8"),
      "Title: Recovery fixture\n",
    );
    assert.equal(
      readFileSync(join(workspace, paths[1]), "utf8"),
      "river mountain river\n",
    );
  }
}

function snapshotFiles(root, relative = "", result = new Map()) {
  for (const entry of readdirSync(join(root, relative), { withFileTypes: true })) {
    const name = join(relative, entry.name);

    if (entry.isDirectory()) {
      snapshotFiles(root, name, result);
    } else if (entry.isFile()) {
      const path = join(root, name);
      result.set(name, {
        bytes: readFileSync(path),
        mtime: statSync(path, { bigint: true }).mtimeNs,
      });
    }
  }

  return result;
}

for (const backend of ["json", "folder", "sqlite"]) {
  test(`1000 steps converge after termination at 50% for ${backend}`, async () => {
    const root = mkdtempSync(join(tmpdir(), "node-control-recovery-"));
    const baseline = join(root, "baseline");
    const recovered = join(root, "recovered");
    let child;
    let exited;

    const options = {
      layout: "hash",
      backend,
      iterations: 1000,
      totalBooks: 500,
      now: new Date(STAMP),
      downloaderFactory: factory,
    };

    try {
      const uninterrupted = await controlStep({
        ...options,
        workspace: baseline,
      });

      assert.equal(uninterrupted.iterations, 1000);
      verifyControlAndArtifacts(baseline, 500);

            const script = `
        import { controlStep } from ${JSON.stringify(CONTROL_URL)};
        import { ControlFiles } from ${JSON.stringify(FILES_URL)};

        const original = ControlFiles.prototype.markIndexedBatch;

        // Pause at 50% while the workspace lock is still held.
        ControlFiles.prototype.markIndexedBatch = function(ids) {
          const result = original.call(this, ids);

          if (this.indexedIds().size === 250) {
            process.send({ type: "halfway" });
            const gate = new Int32Array(new SharedArrayBuffer(4));
            while (true) Atomics.wait(gate, 0, 0);
          }

          return result;
        };

        await controlStep({
          workspace: process.argv[1],
          layout: "hash",
          backend: process.argv[2],
          iterations: 1000,
          totalBooks: 500,
          now: new Date(${JSON.stringify(STAMP)}),
          downloaderFactory: () => ({
            async download() { return ${JSON.stringify(RAW)}; },
            async close() {},
          }),
        });
      `;

      child = spawn(process.execPath, [
        "--input-type=module", "-e", script, recovered, backend,
      ], { stdio: ["ignore", "ignore", "pipe", "ipc"] });

      let stderr = "";
      child.stderr.on("data", (chunk) => { stderr += chunk; });

      exited = new Promise((resolve) => {
        child.once("error", (error) => resolve({ error }));
        child.once("exit", (code, signal) => resolve({ code, signal }));
      });

      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          reject(new Error(`Halfway checkpoint timed out\n${stderr}`));
        }, 120_000);

        child.on("message", (message) => {
          if (message.type === "halfway") {
            clearTimeout(timer);
            resolve();
          }
        });

        exited.then((result) => {
          clearTimeout(timer);
          reject(new Error(`Child exited before checkpoint: ${JSON.stringify(result)}\n${stderr}`));
        });
      });

      verifyControlAndArtifacts(recovered, 250);
      assert.equal(child.kill("SIGKILL"), true);
      const termination = await exited;
      assert.equal(termination.error, undefined);
      assert.notEqual(termination.code, 0);

      const resumed = await controlStep({
        ...options,
        workspace: recovered,
      });

      assert.equal(resumed.iterations, 500);
      verifyControlAndArtifacts(recovered, 500);

      const baselineExport = join(root, "baseline.json");
      const recoveredExport = join(root, "recovered.json");

      await exportIndex({ workspace: baseline, backend, out: baselineExport });
      await exportIndex({ workspace: recovered, backend, out: recoveredExport });

      assert.deepEqual(
        readFileSync(recoveredExport),
        readFileSync(baselineExport),
      );

      // Check observable file contents and modification times on a completed run.
      const before = snapshotFiles(recovered);
      const repeated = await controlStep({
        ...options,
        workspace: recovered,
      });

      assert.equal(repeated.iterations, 0);
      assert.deepEqual(snapshotFiles(recovered), before);
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
      if (exited) await exited;
      rmSync(root, { recursive: true, force: true });
    }
  });
}