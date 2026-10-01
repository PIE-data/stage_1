import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  loadLanguageMap,
  parseHeader,
  parseReleaseDate,
} from "./metadata_parser.js";

const fixtures = JSON.parse(readFileSync(
  new URL("../../python/datamart/fixtures/metadata_headers.json", import.meta.url),
  "utf8",
));

const languageMap = loadLanguageMap();

test("the shared metadata fixture contains ten distinct cases", () => {
  assert.equal(fixtures.length, 10);
  assert.equal(new Set(fixtures.map((item) => item.name)).size, 10);
});

for (const fixture of fixtures) {
  test(`metadata fixture: ${fixture.name}`, () => {
    const warnings = [];

    const actual = parseHeader(fixture.header, {
      languageMap,
      warn: (message) => warnings.push(message),
    });

    assert.deepEqual(actual, fixture.expected);
    assert.deepEqual(
      warnings,
      fixture.name === "missing_title" ? ["MISSING_TITLE"] : [],
    );
  });
}

test("known auxiliary fields do not extend the title", () => {
  for (const field of ["Editor", "Illustrator", "Translator", "Credits"]) {
    const result = parseHeader(
      `Title: Example\n${field}: Someone\n  Additional information\nLanguage: English`,
      { languageMap },
    );

    assert.equal(result.title, "Example");
    assert.equal(result.language, "en");
  }
});

test("URLs in Credits do not become metadata fields", () => {
  const result = parseHeader(
    [
      "Title: Example",
      "Credits: Prepared by volunteers",
      "    http://example.org/archive",
      "    Additional credits",
      "Author: Doe, Jane, 1900-1980",
    ].join("\n"),
    { languageMap },
  );

  assert.deepEqual(result, {
    title: "Example",
    author: "Doe, Jane",
    language: null,
    release_date: null,
  });
});

test("an indented field-like line is a continuation", () => {
  const result = parseHeader(
    "Title: Example\n  Author: A character in the title\nLanguage: English",
    { languageMap },
  );

  assert.equal(result.title, "Example Author: A character in the title");
  assert.equal(result.author, null);
});

test("release dates validate leap years without locale parsing", () => {
  assert.equal(parseReleaseDate("February 29, 2000"), "2000-02-29");
  assert.equal(parseReleaseDate("February 29, 1900"), null);
  assert.equal(parseReleaseDate("April 31, 2020"), null);
  assert.equal(parseReleaseDate("January 0, 2020"), null);
});

test("missing title emits a warning and uses Unknown", () => {
  const warnings = [];
  const result = parseHeader("", {
    languageMap,
    warn: (message) => warnings.push(message),
  });

  assert.deepEqual(result, {
    title: "Unknown",
    author: null,
    language: null,
    release_date: null,
  });
  assert.deepEqual(warnings, ["MISSING_TITLE"]);
});
test("an indented line under Release date is not appended to it (SPEC §5.2)", () => {
  const header = [
    "Title: Pride and Prejudice",
    "Release date: June 1, 1998 [eBook #1342]",
    "                Most recently updated: May 4, 2021",
    "Language: English",
  ].join("\n");

  const parsed = parseHeader(header, { warn: () => {} });
  assert.equal(parsed.release_date, "1998-06-01");
  assert.equal(parsed.language, "en");
});
