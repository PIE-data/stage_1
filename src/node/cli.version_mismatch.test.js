import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("version rejects an incompatible SPEC before executing the command", () => {
  const root = mkdtempSync(join(tmpdir(), "node-spec-mismatch-"));

  try {
    const source = join(root, "src", "node");
    mkdirSync(source, { recursive: true });
    mkdirSync(join(root, "spec"));

    writeFileSync(join(source, "package.json"), '{"type":"module"}\n');
    writeFileSync(join(root, "spec", "SPEC_VERSION"), "999.0.0\n");

    copyFileSync(new URL("./cli.js", import.meta.url), join(source, "cli.js"));
    copyFileSync(
      new URL("./metrics.js", import.meta.url),
      join(source, "metrics.js"),
    );

    const result = spawnSync(process.execPath, [
      join(source, "cli.js"),
      "--workspace", join(root, "workspace"),
      "version",
    ], {
      encoding: "utf8",
      timeout: 10_000,
    });

    assert.ifError(result.error);
    assert.equal(result.status, 1, result.stderr);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /Spec mismatch/u);
    assert.match(result.stderr, /999\.0\.0/u);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});