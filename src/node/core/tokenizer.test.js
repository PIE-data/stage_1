import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  extractRawTokens,
  filterTokens,
  isDigit,
  isWordCharacter,
  loadStopwords,
  normalizeText,
  tokenize,
} from "./tokenizer.js";

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

// This test-only classifier is not the production Unicode implementation.
const TEST_WORD_CHARACTERS = new Set([
  ..."abcdefghijklmnopqrstuvwxyz0123456789",
  "\u{10428}",
]);

function isTestWordCharacter(character) {
  return TEST_WORD_CHARACTERS.has(character);
}

test("token extraction splits separators and emits the final token", () => {
  assert.deepEqual(
    extractRawTokens("hello, world\nlast", isTestWordCharacter),
    [
      { term: "hello", position: 0 },
      { term: "world", position: 1 },
      { term: "last", position: 2 },
    ]
  );
});

test("token extraction preserves both kinds of internal apostrophes", () => {
  assert.deepEqual(
    extractRawTokens("don't l\u2019amour", isTestWordCharacter),
    [
      { term: "don't", position: 0 },
      { term: "l\u2019amour", position: 1 },
    ]
  );
});

test("external and consecutive apostrophes separate tokens", () => {
  assert.deepEqual(
    extractRawTokens("'hello' rock''roll", isTestWordCharacter),
    [
      { term: "hello", position: 0 },
      { term: "rock", position: 1 },
      { term: "roll", position: 2 },
    ]
  );
});

test("raw tokens retain short words, digits and stopwords", () => {
  assert.deepEqual(
    extractRawTokens("a 123 the cat", isTestWordCharacter),
    [
      { term: "a", position: 0 },
      { term: "123", position: 1 },
      { term: "the", position: 2 },
      { term: "cat", position: 3 },
    ]
  );
});

test("token extraction preserves astral code points", () => {
  const letter = "\u{10428}";

  assert.deepEqual(
    extractRawTokens(`${letter}'a`, isTestWordCharacter),
    [{ term: `${letter}'a`, position: 0 }]
  );
});

test("empty input and separators produce no tokens", () => {
  assert.deepEqual(extractRawTokens("", isTestWordCharacter), []);
  assert.deepEqual(extractRawTokens(" ,\n'' ", isTestWordCharacter), []);
});

// Explicit test digits; production Unicode classification is still pending.
const TEST_DIGITS = new Set([..."0123456789", "\u0661", "\u0662"]);

function isTestDigit(character) {
  return TEST_DIGITS.has(character);
}

test("filtering removes rejected tokens without renumbering positions", () => {
  const rawTokens = extractRawTokens("a 123 the cat", isTestWordCharacter);

  assert.deepEqual(
    filterTokens(rawTokens, new Set(["the"]), isTestDigit),
    [{ term: "cat", position: 3 }]
  );

  // Filtering must not modify the original array.
  assert.equal(rawTokens.length, 4);
});

test("filtering keeps lengths 2 and 40 but rejects lengths 1 and 41", () => {
  const rawTokens = [
    { term: "a", position: 0 },
    { term: "ab", position: 1 },
    { term: "a".repeat(40), position: 2 },
    { term: "a".repeat(41), position: 3 },
  ];

  assert.deepEqual(
    filterTokens(rawTokens, new Set(), isTestDigit),
    [rawTokens[1], rawTokens[2]]
  );
});

test("filtering measures astral characters as single code points", () => {
  const letter = "\u{10428}";
  const rawTokens = [
    { term: letter, position: 0 },
    { term: letter.repeat(2), position: 1 },
    { term: letter.repeat(40), position: 2 },
    { term: letter.repeat(41), position: 3 },
  ];

  assert.deepEqual(
    filterTokens(rawTokens, new Set(), isTestDigit),
    [rawTokens[1], rawTokens[2]]
  );
});

test("filtering removes digit-only tokens but preserves mixed tokens", () => {
  const rawTokens = [
    { term: "123", position: 0 },
    { term: "\u0661\u0662", position: 1 },
    { term: "abc123", position: 2 },
    { term: "12'34", position: 3 },
  ];

  assert.deepEqual(
    filterTokens(rawTokens, new Set(), isTestDigit),
    [rawTokens[2], rawTokens[3]]
  );
});

test("stopword filtering uses exact matches", () => {
  const rawTokens = [
    { term: "the", position: 0 },
    { term: "theme", position: 1 },
  ];

  assert.deepEqual(
    filterTokens(rawTokens, new Set(["the"]), isTestDigit),
    [rawTokens[1]]
  );
});

test("the tokenizer combines normalization, extraction and filtering", () => {
  const result = tokenize(
    "A 123 THE CAFÉ",
    new Set(["the"]),
    {
      isWordCharacter: isTestWordCharacter,
      isDigit: isTestDigit,
    }
  );

  assert.deepEqual(result.tokens, [{ term: "cafe", position: 3 }]);
  assert.equal(result.n_tokens_raw, 4);
  assert.equal(result.n_tokens_kept, 1);
});

test("the token checksum has no trailing newline", () => {
  const result = tokenize("ABC", new Set(), {
    isWordCharacter: isTestWordCharacter,
    isDigit: isTestDigit,
  });

  // Known SHA-256 of the UTF-8 bytes of "abc".
  assert.equal(
    result.sha256_tokens,
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
  );
});

test("fully filtered input produces the checksum of an empty byte sequence", () => {
  const result = tokenize("a 123 the", new Set(["the"]), {
    isWordCharacter: isTestWordCharacter,
    isDigit: isTestDigit,
  });

  assert.deepEqual(result.tokens, []);
  assert.equal(result.n_tokens_raw, 3);
  assert.equal(result.n_tokens_kept, 0);
  assert.equal(
    result.sha256_tokens,
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  );
});

test("Unicode classification recognizes letters and decimal digits", () => {
  for (const character of ["a", "Ω", "中", "\u{10428}", "7", "\u0661"]) {
    assert.equal(isWordCharacter(character), true, character);
  }

  for (const character of ["_", "'", "-", "\u0301", "😀", "ab", ""]) {
    assert.equal(isWordCharacter(character), false, character);
  }
});

test("digit classification accepts only Unicode decimal digits", () => {
  for (const character of ["0", "\u0661", "\uFF12"]) {
    assert.equal(isDigit(character), true, character);
  }

  for (const character of ["a", "²", "Ⅳ", "12", ""]) {
    assert.equal(isDigit(character), false, character);
  }
});

test("the default tokenizer handles Unicode and preserves raw positions", () => {
  const result = tokenize(
    "THE CAFÉ 中文 \u0661\u0662 \u{10400}\u{10400}",
    new Set(["the"])
  );

  assert.equal(result.n_tokens_raw, 5);
  assert.equal(result.n_tokens_kept, 3);
  assert.deepEqual(result.tokens, [
    { term: "cafe", position: 1 },
    { term: "中文", position: 2 },
    { term: "\u{10428}\u{10428}", position: 4 },
  ]);
});

test("the default tokenizer joins apostrophes between Unicode letters", () => {
  const result = tokenize("中文\u2019中文", new Set());

  assert.deepEqual(result.tokens, [
    { term: "中文\u2019中文", position: 0 },
  ]);
});