import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { loadStopwords, tokenize } from "../core/tokenizer.js";
import { splitText } from "../datalake/splitter.js";
import { FolderIndex } from "./folder_index.js";

const golden = new URL("../../../spec/golden/", import.meta.url);

test("Folder canonical export matches the frozen hash on 20 golden books", (t) => {
  const workspace = mkdtempSync(join(tmpdir(), "node-folder-golden-"));
  t.after(() => rmSync(workspace, { recursive: true, force: true }));

  const ids = readFileSync(new URL("manifest_20.txt", golden), "utf8")
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"))
    .map(Number);

  assert.equal(ids.length, 20);
  assert.equal(new Set(ids).size, 20);
  assert.ok(ids.every((id) => Number.isSafeInteger(id) && id > 0));

  const stopwords = loadStopwords();
  const index = new FolderIndex(workspace, { positions: true });

  for (const bookId of ids) {
    const raw = readFileSync(new URL(`${bookId}.txt`, golden), "utf8");
    const { body } = splitText(raw);
    const { tokens } = tokenize(body, stopwords);
    index.writeBook(bookId, tokens);
  }

  const output = join(workspace, "canonical.json");
  index.exportCanonical(output);

  const bytes = readFileSync(output);
  const actual = createHash("sha256").update(bytes).digest("hex");
  const expected = readFileSync(
    new URL("expected.sha256", golden),
    "utf8",
  ).trim().split(/\s+/u)[0];

  assert.match(expected, /^[a-f0-9]{64}$/u);
  assert.notEqual(bytes.at(-1), 10, "Canonical output must not end with LF");
  assert.equal(actual, expected);
});