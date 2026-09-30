import test from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
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
import { fileURLToPath } from "node:url";

const CLI = fileURLToPath(new URL("./cli.js", import.meta.url));
const INGESTION = new URL("./ingestion.js", import.meta.url).href;
const CONTROL = new URL("./control/files.js", import.meta.url).href;
const STAMP = "2026-09-17T14:03:11Z";

const RAW = [
  "Title: Recovery",
  "Language: English",
  "*** START OF THE PROJECT GUTENBERG EBOOK RECOVERY ***",
  "Recovered body",
  "*** END OF THE PROJECT GUTENBERG EBOOK RECOVERY ***",
].join("\n");

function runCli(workspace, ...args) {
  return spawnSync(process.execPath, [
    CLI,
    "--workspace", workspace,
    "--datalake-layout", "hash",
    "--now", STAMP,
    ...args,
  ], {
    encoding: "utf8",
    timeout: 10_000,
  });
}

test("reconcile recovers after termination before the control append", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "ingestion-recovery-"));
  let child;
  let exited;
  let stderr = "";

  try {
    mkdirSync(join(workspace, "raw"));
    writeFileSync(join(workspace, "raw", "42.txt"), RAW, "utf8");

    // Pause at the exact boundary after artifacts and receipt, before append.
    // The hook exists only inside this test process.
        const script = `
      import { splitCachedBook } from ${JSON.stringify(INGESTION)};
      import { ControlFiles } from ${JSON.stringify(CONTROL)};

      ControlFiles.prototype.markDownloaded = function () {
        process.send({ type: "before-control-append" });

        // Stay inside the locked operation until the parent kills this process.
        const gate = new Int32Array(new SharedArrayBuffer(4));
        while (true) {
          Atomics.wait(gate, 0, 0);
        }
      };

      await splitCachedBook({
        workspace: process.argv[1],
        bookId: 42,
        layout: "hash",
        now: new Date(${JSON.stringify(STAMP)}),
      });
    `;

    child = spawn(process.execPath, [
      "--input-type=module",
      "-e",
      script,
      workspace,
    ], {
      stdio: ["ignore", "ignore", "pipe", "ipc"],
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    exited = new Promise((resolveExit) => {
      child.once("exit", (code, signal) => {
        resolveExit({ code, signal });
      });
      child.once("error", (error) => resolveExit({ error }));
    });

    await new Promise((resolveReady, rejectReady) => {
      const timer = setTimeout(() => {
        rejectReady(new Error(`Checkpoint timed out: ${stderr}`));
      }, 10_000);

      child.once("message", (message) => {
        clearTimeout(timer);

        if (message.type === "before-control-append") {
          resolveReady();
        } else {
          rejectReady(new Error("Unexpected child message"));
        }
      });

      exited.then(() => {
        clearTimeout(timer);
        rejectReady(new Error(`Child exited before checkpoint: ${stderr}`));
      });
    });

    const downloadedPath = join(
      workspace, "control", "downloaded_books.txt",
    );
    const bodyPath = join(
      workspace, "datalake", "00", "00", "42.body.txt",
    );
    const receiptPath = join(
      workspace, "control", "ingestion", "hash", "42.json",
    );

    assert.equal(readFileSync(bodyPath, "utf8"), "Recovered body\n");
    const originalReceipt = readFileSync(receiptPath, "utf8");
    assert.equal(existsSync(downloadedPath), false);

    assert.equal(child.kill("SIGKILL"), true);
    const termination = await exited;
    assert.equal(termination.error, undefined);
    assert.notEqual(termination.code, 0);

    // A fresh process must acquire the released lock and repair control state.
    const repaired = runCli(workspace, "reconcile");
    assert.equal(repaired.error, undefined);
    assert.equal(repaired.status, 0, repaired.stderr);
    assert.equal(repaired.stdout, "");
    assert.equal(readFileSync(downloadedPath, "utf8"), "42\n");
    assert.equal(readFileSync(receiptPath, "utf8"), originalReceipt);

    const resumed = runCli(workspace, "split", "--book-id", "42");
    assert.equal(resumed.error, undefined);
    assert.equal(resumed.status, 0, resumed.stderr);
    assert.equal(readFileSync(downloadedPath, "utf8"), "42\n");
    assert.equal(readFileSync(bodyPath, "utf8"), "Recovered body\n");
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
    if (exited) await exited;
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("reconcile removes a partial body after termination and split can resume", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "partial-recovery-"));
  let child;
  let exited;
  let stderr = "";

  try {
    mkdirSync(join(workspace, "raw"));
    writeFileSync(join(workspace, "raw", "42.txt"), RAW, "utf8");

    const bodyPath = join(
      workspace, "datalake", "00", "00", "42.body.txt",
    );
    const partialPath = `${bodyPath}.part`;
    const downloadedPath = join(
      workspace, "control", "downloaded_books.txt",
    );

    // Inject the interruption only in this child process.
    // The real atomic writer writes and fsyncs the body before this checkpoint.
    const script = `
      import fs from "node:fs";
      import { syncBuiltinESMExports } from "node:module";

      const originalRename = fs.renameSync;

      fs.renameSync = function (source, target) {
        if (String(target).endsWith("42.body.txt")) {
          process.send({ type: "before-body-rename" });

          const gate = new Int32Array(new SharedArrayBuffer(4));
          while (true) {
            Atomics.wait(gate, 0, 0);
          }
        }

        return originalRename(source, target);
      };

      syncBuiltinESMExports();

      const { splitCachedBook } = await import(
        ${JSON.stringify(INGESTION)}
      );

      await splitCachedBook({
        workspace: process.argv[1],
        bookId: 42,
        layout: "hash",
        now: new Date(${JSON.stringify(STAMP)}),
      });
    `;

    child = spawn(process.execPath, [
      "--input-type=module",
      "-e",
      script,
      workspace,
    ], {
      stdio: ["ignore", "ignore", "pipe", "ipc"],
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    exited = new Promise((resolveExit) => {
      child.once("exit", (code, signal) => {
        resolveExit({ code, signal });
      });
      child.once("error", (error) => resolveExit({ error }));
    });

    await new Promise((resolveReady, rejectReady) => {
      const timer = setTimeout(() => {
        rejectReady(new Error(`Checkpoint timed out: ${stderr}`));
      }, 10_000);

      child.once("message", (message) => {
        clearTimeout(timer);

        if (message.type === "before-body-rename") {
          resolveReady();
        } else {
          rejectReady(new Error("Unexpected child message"));
        }
      });

      exited.then(() => {
        clearTimeout(timer);
        rejectReady(new Error(`Child exited before checkpoint: ${stderr}`));
      });
    });

    assert.equal(existsSync(partialPath), true);
    assert.equal(existsSync(bodyPath), false);
    assert.equal(existsSync(downloadedPath), false);

    assert.equal(child.kill("SIGKILL"), true);
    const termination = await exited;
    assert.equal(termination.error, undefined);
    assert.notEqual(termination.code, 0);

    // Forced termination bypasses atomicWrite's finally cleanup.
    assert.equal(existsSync(partialPath), true);

    const repaired = runCli(workspace, "reconcile");
    assert.equal(repaired.error, undefined);
    assert.equal(repaired.status, 0, repaired.stderr);
    assert.equal(existsSync(partialPath), false);

    // A header without its final body must not count as downloaded.
    assert.equal(readFileSync(downloadedPath, "utf8"), "");

    const resumed = runCli(workspace, "split", "--book-id", "42");
    assert.equal(resumed.error, undefined);
    assert.equal(resumed.status, 0, resumed.stderr);

    assert.equal(readFileSync(bodyPath, "utf8"), "Recovered body\n");
    assert.equal(readFileSync(downloadedPath, "utf8"), "42\n");
    assert.equal(existsSync(partialPath), false);

    const receipt = JSON.parse(readFileSync(
      join(workspace, "control", "ingestion", "hash", "42.json"),
      "utf8",
    ));
    assert.equal(receipt.book_id, 42);
    assert.equal(receipt.ingested_at, STAMP);
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
    if (exited) await exited;
    rmSync(workspace, { recursive: true, force: true });
  }
});