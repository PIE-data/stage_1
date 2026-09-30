import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const cli = fileURLToPath(new URL("./cli.js", import.meta.url));

function run(workspace, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [
      cli,
      "--workspace", workspace,
      "--datalake-layout", "hash",
      "--index-backend", "json",
      "--now", "2026-01-01T00:00:00Z",
      ...args,
    ]);

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill(), 15_000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (text) => { stdout += text; });
    child.stderr.on("data", (text) => { stderr += text; });

    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });

    child.on("close", (status) => {
      clearTimeout(timer);
      resolve({ status, stdout, stderr });
    });
  });
}

test("CLI control-step downloads, indexes and stops when exhausted", async () => {
  const root = mkdtempSync(join(tmpdir(), "node-cli-control-"));
  const requests = [];

  const server = createServer((request, response) => {
    requests.push(request.url);
    response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
    response.end([
      "Title: Control fixture",
      "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***",
      "river mountain",
      "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***",
    ].join("\n"));
  });

  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });

    const manifest = join(root, "manifest.txt");
    writeFileSync(manifest, "42\n");
    const source = `http://127.0.0.1:${server.address().port}`;

    const args = [
      "control-step",
      "--iterations", "10",
      "--manifest", manifest,
      "--source-base", source,
    ];

    const first = await run(root, args);
    assert.equal(first.status, 0, first.stderr);
    assert.equal(first.stdout, "");
    assert.match(first.stderr, /iterations 2, downloaded 1, indexed 1, failed 0/u);

    assert.equal(
      readFileSync(join(root, "control/indexed_books.txt"), "utf8"),
      "42\n",
    );
    assert.deepEqual(requests, ["/cache/epub/42/pg42.txt"]);

    const second = await run(root, args);
    assert.equal(second.status, 0, second.stderr);
    assert.match(second.stderr, /iterations 0/u);
    assert.equal(requests.length, 1);

    const queried = await run(root, [
      "query", "--terms", "river", "--mode", "and",
    ]);
    assert.equal(queried.status, 0, queried.stderr);
    assert.equal(queried.stdout, "42\n");
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    rmSync(root, { recursive: true, force: true });
  }
});

test("CLI control-step rejects invalid arguments", async () => {
  const root = mkdtempSync(join(tmpdir(), "node-cli-control-args-"));

  try {
    for (const args of [
      ["control-step"],
      ["control-step", "--iterations", "0"],
      ["control-step", "--iterations", "1", "--total-books", "0"],
      ["control-step", "--iterations", "1", "--source-base", "invalid"],
    ]) {
      const result = await run(root, args);
      assert.equal(result.status, 2, result.stderr);
      assert.equal(result.stdout, "");
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});