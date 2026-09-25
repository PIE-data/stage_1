import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { loadStopwords, normalizeText } from "./tokenizer.js";

test("normalization converts uppercase text and removes accents", () => {
  assert.equal(normalizeText("CAFÉ NAÏVE"), "cafe naive");
});

test("normalization applies Unicode compatibility normalization", () => {
  // Fullwidth letters and compatibility ligatures become ordinary letters.
  assert.equal(normalizeText("\uFF21\uFF22\uFF23 \uFB01"), "abc fi");
});

test("normalization handles decomposed accents", () => {
  assert.equal(normalizeText("Cafe\u0301"), "cafe");
});

test("normalization preserves non-Latin letters and punctuation", () => {
  assert.equal(normalizeText("中文 — DON'T"), "中文 — don't");
});

test("normalization handles an empty string", () => {
  assert.equal(normalizeText(""), "");
});

test("stopword loading ignores comments, blank lines and duplicates", () => {
  const directory = mkdtempSync(join(tmpdir(), "stage1-stopwords-"));

  try {
    const path = join(directory, "stopwords.txt");
    writeFileSync(path, "# Test list\n\nthe\nand\nthe\n", "utf8");

    assert.deepEqual(loadStopwords(path), new Set(["the", "and"]));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("the shared stopword list is available", () => {
  const words = loadStopwords();

  assert.ok(words instanceof Set);
  assert.ok(words.size > 0);
});