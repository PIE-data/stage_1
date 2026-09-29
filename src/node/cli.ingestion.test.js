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
import { acquireRunLock } from "./control/run_lock.js";
import { createServer } from "node:http";

const cliPath = fileURLToPath(new URL("./cli.js", import.meta.url));
const STAMP = "2026-09-17T14:03:11Z";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "cli-ingestion-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

function run(workspace, ...args) {
  const result = spawnSync(
    process.execPath,
    [cliPath, "--workspace", workspace, ...args],
    { encoding: "utf8", timeout: 10000 },
  );

  assert.ifError(result.error);
  return result;
}

function cache(workspace, text) {
  mkdirSync(join(workspace, "raw"), { recursive: true });
  writeFileSync(join(workspace, "raw", "42.txt"), text, "utf8");
}

test("CLI split writes the selected layout and returns 0", (t) => {
  const workspace = fixture(t);

  cache(workspace, [
    "Title: Café",
    "*** START OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
    "café",
    "*** END OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
  ].join("\n"));

  const result = run(
    workspace,
    "--datalake-layout", "hash",
    "--now", STAMP,
    "split", "--book-id", "42",
  );

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "");

  assert.equal(
    readFileSync(
      join(workspace, "datalake", "00", "00", "42.body.txt"),
      "utf8",
    ),
    "café\n",
  );
});

test("CLI split returns 3 for absent raw input", (t) => {
  const workspace = fixture(t);
  const result = run(workspace, "split", "--book-id", "42");

  assert.equal(result.status, 3, result.stderr);
  assert.equal(result.stdout, "");
});

test("CLI split returns 3 and records missing markers", (t) => {
  const workspace = fixture(t);
  cache(workspace, "No markers");

  const result = run(
    workspace,
    "--now", STAMP,
    "split", "--book-id", "42",
  );

  assert.equal(result.status, 3, result.stderr);
  assert.equal(existsSync(join(workspace, "datalake")), false);

  assert.equal(
    readFileSync(
      join(workspace, "control", "failed_books.txt"),
      "utf8",
    ),
    `42\tNO_MARKERS\t${STAMP}\n`,
  );
});

test("CLI rejects invalid ingestion arguments with exit code 2", (t) => {
  const workspace = fixture(t);

  const cases = [
    ["split"],
    ["split", "--book-id", "0"],
    ["split", "--book-id", "42", "--workers", "2"],
    ["download"],
    ["download", "--book-id", "42", "--manifest", "books.txt"],
    ["download", "--book-id", "42", "--workers", "0"],
    ["download", "--book-id", "42", "--source-base", "file:///tmp"],
    ["--datalake-layout", "invalid", "split", "--book-id", "42"],
    ["--now", "2026-02-30T00:00:00Z", "split", "--book-id", "42"],
  ];

  for (const args of cases) {
    const result = run(workspace, ...args);
    assert.equal(result.status, 2, `${args.join(" ")}\n${result.stderr}`);
    assert.equal(result.stdout, "");
  }

  assert.equal(existsSync(join(workspace, "control")), false);
});

test("CLI split returns 4 when another process holds the lock", async (t) => {
  const workspace = fixture(t);
  const lock = await acquireRunLock(workspace);

  try {
    const result = run(workspace, "split", "--book-id", "42");
    assert.equal(result.status, 4, result.stderr);
    assert.equal(result.stdout, "");
  } finally {
    await lock.release();
  }
});

test("CLI download skips a completed book without contacting the source", (t) => {
  const workspace = fixture(t);

  mkdirSync(join(workspace, "control"));
  writeFileSync(
    join(workspace, "control", "downloaded_books.txt"),
    "42\n",
    "utf8",
  );

  const result = run(
    workspace,
    "download", "--book-id", "42",
    "--source-base", "http://127.0.0.1:1",
  );

  assert.equal(result.status, 0, result.stderr);
  assert.equal(existsSync(join(workspace, "raw")), false);
});

function runAsync(workspace, ...args) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(
      process.execPath,
      [cliPath, "--workspace", workspace, ...args],
      { stdio: ["ignore", "pipe", "pipe"] },
    );

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, 15000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });

    child.on("close", (status) => {
      clearTimeout(timer);

      if (timedOut) {
        reject(new Error(`CLI timed out:\n${stderr}`));
      } else {
        resolveResult({ status, stdout, stderr });
      }
    });
  });
}

test("CLI download processes a local HTTP manifest and skips completed books", async (t) => {
  const workspace = fixture(t);
  const requests = [];

  const raw = [
    "Title: Café",
    "Language: English",
    "*** START OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
    "café",
    "*** END OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
    "Discarded footer",
  ].join("\n");

  const server = createServer((request, response) => {
    requests.push(request.url);

    if (request.url === "/cache/epub/42/pg42.txt") {
      response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
      response.end(raw);
    } else if (request.url === "/cache/epub/43/pg43.txt") {
      response.writeHead(200);
      response.end("Text without markers");
    } else {
      response.writeHead(404);
      response.end("Not found");
    }
  });

  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });

  try {
    const sourceBase = `http://127.0.0.1:${server.address().port}`;
    const manifest = join(workspace, "manifest.txt");
    writeFileSync(manifest, "42\n43\n44\n", "utf8");

    const result = await runAsync(
      workspace,
      "--datalake-layout", "book",
      "--now", STAMP,
      "download",
      "--manifest", manifest,
      "--workers", "2",
      "--source-base", sourceBase,
    );

    assert.equal(result.status, 3, result.stderr);
    assert.equal(result.stdout, "");

    assert.deepEqual([...requests].sort(), [
      "/cache/epub/42/pg42.txt",
      "/cache/epub/43/pg43.txt",
      "/cache/epub/44/pg44.txt",
    ]);

    assert.equal(
      readFileSync(join(workspace, "raw", "42.txt"), "utf8"),
      raw,
    );

    assert.equal(
      readFileSync(join(workspace, "raw", "43.txt"), "utf8"),
      "Text without markers",
    );

    assert.equal(existsSync(join(workspace, "raw", "44.txt")), false);

    assert.deepEqual(
      readFileSync(
        join(workspace, "datalake", "books", "42", "body.txt"),
      ),
      Buffer.from("café\n", "utf8"),
    );

    const metadata = JSON.parse(readFileSync(
      join(workspace, "datalake", "books", "42", "meta.json"),
      "utf8",
    ));

    assert.equal(metadata.title, "Café");
    assert.equal(metadata.language, "en");
    assert.equal(metadata.ingested_at, STAMP);

    assert.equal(
      readFileSync(
        join(workspace, "control", "downloaded_books.txt"),
        "utf8",
      ),
      "42\n",
    );

    assert.equal(
      readFileSync(
        join(workspace, "control", "failed_books.txt"),
        "utf8",
      ),
      `43\tNO_MARKERS\t${STAMP}\n44\tNOT_FOUND\t${STAMP}\n`,
    );

    assert.equal(
      existsSync(join(workspace, "datalake", "books", "43")),
      false,
    );

    const requestCount = requests.length;

    const repeated = await runAsync(
      workspace,
      "--datalake-layout", "book",
      "--now", STAMP,
      "download",
      "--book-id", "42",
      "--source-base", sourceBase,
    );

    assert.equal(repeated.status, 0, repeated.stderr);
    assert.equal(requests.length, requestCount);
  } finally {
    await new Promise((resolveClose, reject) => {
      server.close((error) => {
        if (error) reject(error);
        else resolveClose();
      });
      server.closeAllConnections();
    });
  }
});