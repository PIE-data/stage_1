import test from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
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
import {
  acquireRunLock,
  withRunLock,
} from "./run_lock.js";

const moduleUrl = new URL("./run_lock.js", import.meta.url).href;

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "run-lock-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

function contender(workspace) {
  const script = `
    import {
      acquireRunLock,
      WorkspaceLockedError,
    } from ${JSON.stringify(moduleUrl)};

    try {
      const lock = await acquireRunLock(process.argv[1]);
      await lock.release();
    } catch (error) {
      if (error instanceof WorkspaceLockedError) {
        process.exitCode = 4;
      } else {
        console.error(error);
        process.exitCode = 1;
      }
    }
  `;

  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", script, workspace],
    { encoding: "utf8", timeout: 10000 },
  );

  assert.ifError(result.error);
  return result;
}

test("an existing lock file is reusable and is not truncated", async (t) => {
  const workspace = fixture(t);
  const directory = join(workspace, "control");
  const path = join(directory, "run.lock");

  mkdirSync(directory);
  writeFileSync(path, "existing file\n", "utf8");

  const lock = await acquireRunLock(workspace);
  try {
    assert.equal(lock.path, path);
  } finally {
    await lock.release();
  }

  // Releasing twice must be safe.
  await lock.release();

  // Inspect the contents after release for Windows compatibility.
  assert.equal(existsSync(path), true);
  assert.equal(readFileSync(path, "utf8"), "existing file\n");

  const result = contender(workspace);
  assert.equal(result.status, 0, result.stderr);
});

test("a second process is rejected until the lock is released", async (t) => {
  const workspace = fixture(t);
  const lock = await acquireRunLock(workspace);

  try {
    const result = contender(workspace);
    assert.equal(result.status, 4, result.stderr);
    assert.equal(result.stdout, "");
  } finally {
    await lock.release();
  }

  const result = contender(workspace);
  assert.equal(result.status, 0, result.stderr);
});

test("withRunLock returns results and releases after an exception", async (t) => {
  const workspace = fixture(t);

  assert.equal(
    await withRunLock(workspace, async () => 42),
    42,
  );

  await assert.rejects(
    withRunLock(workspace, async () => {
      throw new Error("Operation failed");
    }),
    /Operation failed/,
  );

  const result = contender(workspace);
  assert.equal(result.status, 0, result.stderr);
});

test("terminating the owner releases the operating-system lock", {
  timeout: 15000,
}, async (t) => {
  const workspace = fixture(t);

  const script = `
    import { acquireRunLock } from ${JSON.stringify(moduleUrl)};

    const lock = await acquireRunLock(process.argv[1]);
    process.send({ ready: true });
    setInterval(() => {}, 1000);
  `;

  const child = spawn(
    process.execPath,
    ["--input-type=module", "-e", script, workspace],
    { stdio: ["ignore", "ignore", "pipe", "ipc"] },
  );

  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  // Register immediately so an early child exit cannot be missed.
  const closed = new Promise((resolveClose) => {
    child.once("close", resolveClose);
  });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);

  try {
    const [message] = await Promise.race([
      once(child, "message", { signal: controller.signal }),
      closed.then(() => {
        throw new Error(`Lock owner exited before readiness: ${stderr}`);
      }),
    ]);

    clearTimeout(timer);
    assert.equal(message.ready, true);

    const blocked = contender(workspace);
    assert.equal(blocked.status, 4, blocked.stderr);

    // Force termination without calling release().
    assert.equal(child.kill("SIGKILL"), true);
    await closed;

    const recovered = contender(workspace);
    assert.equal(recovered.status, 0, recovered.stderr);
    assert.equal(existsSync(join(workspace, "control", "run.lock")), true);
  } finally {
    clearTimeout(timer);
    controller.abort();

    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }

    await closed;
  }
});