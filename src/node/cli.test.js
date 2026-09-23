import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const cliPath = fileURLToPath(new URL("./cli.js", import.meta.url));
const versionFile = new URL("../../spec/SPEC_VERSION", import.meta.url);

test("version prints the specification version and exits successfully", () => {
  const expectedVersion = readFileSync(versionFile, "utf8").trim();

  // Run the CLI as a separate process using the current Node executable.
  const result = spawnSync(
    process.execPath,
    [cliPath, "--workspace", ".", "version"],
    { encoding: "utf8" }
  );

  assert.ifError(result.error);
  assert.equal(result.status, 0);
  assert.equal(result.stdout, `${expectedVersion}\n`);
});

test("an unknown command exits with code 2", () => {
  const result = spawnSync(
    process.execPath,
    [cliPath, "--workspace", ".", "unknown"],
    { encoding: "utf8" }
  );

  assert.ifError(result.error);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /Usage:/);
});

test("a missing workspace exits with code 2", () => {
  const result = spawnSync(
    process.execPath,
    [cliPath, "version"],
    { encoding: "utf8" }
  );

  assert.ifError(result.error);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /Usage:/);
});