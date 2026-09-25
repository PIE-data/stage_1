import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { loadStopwords, tokenize } from "./tokenizer.js";

const GOLDEN_DIRECTORY = new URL(
  "../../../spec/golden/",
  import.meta.url
);

function readGoldenFile(name) {
  return readFileSync(new URL(name, GOLDEN_DIRECTORY), "utf8");
}

const bookIds = readGoldenFile("manifest_20.txt")
  .trim()
  .split(/\s+/u)
  .map(Number);

const expectedRecords = readGoldenFile("tokens_20.jsonl")
  .trim()
  .split(/\r?\n/u)
  .map((line) => JSON.parse(line));

const expectedById = new Map(
  expectedRecords.map((record) => [record.book_id, record])
);

const stopwords = loadStopwords();

function extractGoldenBody(rawText) {
  // Normalize line endings before locating the complete marker lines.
  const lines = rawText
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n");

  const startMarker = "*** START OF THE PROJECT GUTENBERG EBOOK";
  const endMarker = "*** END OF THE PROJECT GUTENBERG EBOOK";

  const startIndex = lines.findIndex((line) => line.includes(startMarker));
  const endIndex = lines.findLastIndex((line) => line.includes(endMarker));

  assert.ok(startIndex >= 0, "START marker is missing");
  assert.ok(endIndex > startIndex, "END marker must follow START");

  // Exclude both marker lines, the header and the footer.
  let body = lines.slice(startIndex + 1, endIndex).join("\n");

  // Apply body cleaning from SPEC.md §2.3.
  if (body.startsWith("\uFEFF")) {
    body = body.slice(1);
  }

  body = body
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/u, ""))
    .join("\n")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();

  return `${body}\n`;
}

test("golden assets contain exactly 20 matching book IDs", () => {
  assert.equal(bookIds.length, 20);
  assert.equal(new Set(bookIds).size, 20);
  assert.ok(bookIds.every((id) => Number.isSafeInteger(id) && id > 0));

  assert.equal(expectedRecords.length, 20);
  assert.equal(expectedById.size, 20);
  assert.deepEqual(new Set(bookIds), new Set(expectedById.keys()));
});

for (const bookId of bookIds) {
  test(`golden tokenizer matches book ${bookId}`, () => {
    const expected = expectedById.get(bookId);
    assert.ok(expected, `Missing expected record for book ${bookId}`);

    const rawText = readGoldenFile(`${bookId}.txt`);
    const body = extractGoldenBody(rawText);
    const result = tokenize(body, stopwords);

    assert.deepEqual(
      {
        book_id: bookId,
        n_tokens_raw: result.n_tokens_raw,
        n_tokens_kept: result.n_tokens_kept,
        sha256_tokens: result.sha256_tokens,
      },
      expected
    );
  });
}