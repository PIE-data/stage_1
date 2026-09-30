import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../", import.meta.url));
const python = process.env.PYTHON || "python";
const temporary = mkdtempSync(join(tmpdir(), "query-parity-"));
const stamp = "2026-01-01T00:00:00Z";

function lines(path) {
  return readFileSync(path, "utf8")
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
}

const ids = lines(join(repo, "spec/golden/manifest_20.txt"));
const expected = readFileSync(
  join(repo, "spec/golden/expected.sha256"),
  "utf8",
).trim().split(/\s+/u)[0];

const workloads = [
  "single.txt",
  "and2.txt",
  "and3.txt",
  "absent.txt",
].map((name) => ({
  name,
  queries: lines(join(repo, "spec/queries", name)),
}));

function invoke(language, workspace, backend, args) {
  const executable = language === "node" ? process.execPath : python;
  const entry = join(
    repo,
    language === "node" ? "src/node/cli.js" : "src/python/cli.py",
  );

  const result = spawnSync(executable, [
    entry,
    "--workspace", workspace,
    "--datalake-layout", "hash",
    "--index-backend", backend,
    "--now", stamp,
    ...args,
  ], {
    cwd: repo,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
    },
    timeout: 600_000,
    maxBuffer: 16 * 1024 * 1024,
  });

  if (result.error) throw result.error;

  assert.equal(
    result.status,
    0,
    `${language}/${backend}: ${args.join(" ")}\n` +
      result.stderr.toString("utf8"),
  );

  return result.stdout;
}

function verifyHash(language, workspace, backend) {
  const output = join(temporary, `${language}-${backend}-canonical.json`);
  invoke(language, workspace, backend, [
    "export-canonical", "--out", output,
  ]);

  const actual = createHash("sha256")
    .update(readFileSync(output))
    .digest("hex");

  assert.equal(actual, expected, `${language}/${backend}: golden hash mismatch`);
}

try {
  assert.equal(ids.length, 20);
  assert.equal(new Set(ids).size, 20);

  let comparisons = 0;

  for (const backend of ["json", "folder", "sqlite"]) {
    const workspaces = {};

    for (const language of ["python", "node"]) {
      const workspace = join(temporary, `${language}-${backend}`);
      workspaces[language] = workspace;
      mkdirSync(join(workspace, "raw"), { recursive: true });

      console.log(`${language}/${backend}: splitting 20 golden books`);

      for (const id of ids) {
        copyFileSync(
          join(repo, "spec/golden", `${id}.txt`),
          join(workspace, "raw", `${id}.txt`),
        );
        invoke(language, workspace, backend, ["split", "--book-id", id]);
      }

      console.log(`${language}/${backend}: indexing; folder may take several minutes`);
      invoke(language, workspace, backend, ["index", "--all", "--positions"]);
      verifyHash(language, workspace, backend);
      console.log(`${language}/${backend}: golden hash OK`);
    }

    for (const { name, queries } of workloads) {
      for (let number = 0; number < queries.length; number += 1) {
        const terms = queries[number];
        const args = ["query", "--terms", terms, "--mode", "and"];

        const reference = invoke("python", workspaces.python, backend, args);
        const actual = invoke("node", workspaces.node, backend, args);

        assert.ok(
          actual.equals(reference),
          `${backend}/${name}:${number + 1}: ${JSON.stringify(terms)}\n` +
            `Python stdout: ${JSON.stringify(reference.toString("utf8"))}\n` +
            `Node stdout:   ${JSON.stringify(actual.toString("utf8"))}`,
        );

        comparisons += 1;

        if ((number + 1) % 25 === 0 || number + 1 === queries.length) {
          console.log(`${backend}/${name}: ${number + 1}/${queries.length} OK`);
        }
      }
    }
  }

  console.log(`PASS: ${comparisons} byte-identical query outputs`);
} finally {
  rmSync(temporary, { recursive: true, force: true });
}