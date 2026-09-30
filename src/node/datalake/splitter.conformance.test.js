import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { splitText } from "./splitter.js";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));
const GOLDEN = new URL("../../../spec/golden/", import.meta.url);

// PYTHON can select a specific interpreter in CI or on a developer machine.
const PYTHON = process.env.PYTHON || "python";

// This adapter only invokes the reference implementation.
// It does not reproduce or inspect its splitting logic.
const REFERENCE_SCRIPT = `
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src" / "python"))
from datalake.splitter import split_text

raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
header, body = split_text(raw)

print(json.dumps({
    "header": base64.b64encode(header.encode("utf-8")).decode("ascii"),
    "body": base64.b64encode(body.encode("utf-8")).decode("ascii"),
}))
`;

const bookIds = readFileSync(
  new URL("manifest_20.txt", GOLDEN),
  "utf8",
).trim().split(/\s+/u).map(Number);

test("split conformance covers exactly 20 distinct books", () => {
  assert.equal(bookIds.length, 20);
  assert.equal(new Set(bookIds).size, 20);
  assert.ok(bookIds.every((id) => Number.isSafeInteger(id) && id > 0));
});

for (const bookId of bookIds) {
  test(`split bytes match the Python reference for book ${bookId}`, () => {
    const raw = readFileSync(new URL(`${bookId}.txt`, GOLDEN));

    const reference = spawnSync(PYTHON, ["-c", REFERENCE_SCRIPT], {
      cwd: REPO,
      input: raw,
      maxBuffer: 32 * 1024 * 1024,
      timeout: 30_000,
      windowsHide: true,
    });

    assert.ifError(reference.error);
    assert.equal(
      reference.status,
      0,
      reference.stderr?.toString("utf8") || "Python reference failed",
    );

    const expected = JSON.parse(reference.stdout.toString("utf8"));
    const actual = splitText(raw.toString("utf8"));

    // Base64 transport avoids newline conversion through Windows pipes.
    for (const field of ["header", "body"]) {
      const actualBytes = Buffer.from(actual[field], "utf8");
      const expectedBytes = Buffer.from(expected[field], "base64");

      assert.equal(
        actualBytes.length,
        expectedBytes.length,
        `Book ${bookId}: ${field} byte length differs`,
      );

      assert.ok(
        actualBytes.equals(expectedBytes),
        `Book ${bookId}: ${field} bytes differ`,
      );
    }
  });
}