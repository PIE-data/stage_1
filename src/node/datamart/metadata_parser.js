import { readFileSync } from "node:fs";

const LANGUAGE_MAP = new URL(
  "../../../spec/language_map.txt",
  import.meta.url,
);

const MONTHS = new Map([
  ["january", 1],
  ["february", 2],
  ["march", 3],
  ["april", 4],
  ["may", 5],
  ["june", 6],
  ["july", 7],
  ["august", 8],
  ["september", 9],
  ["october", 10],
  ["november", 11],
  ["december", 12],
]);

const FIELD = /^(Title|Author|Editor|Illustrator|Translator|Release date|Language|Credits):[ \t]*(.*)$/iu;

function normalizeWhitespace(value) {
  return value.replace(/\s+/gu, " ").trim();
}

export function loadLanguageMap(path = LANGUAGE_MAP) {
  const mapping = new Map();

  for (const rawLine of readFileSync(path, "utf8").split(/\r\n|\n|\r/u)) {
    const line = rawLine.trim();

    if (!line || line.startsWith("#")) {
      continue;
    }

    const separator = line.indexOf("=");

    if (separator <= 0 || separator === line.length - 1) {
      throw new Error(`Invalid language mapping: ${line}`);
    }

    const name = line.slice(0, separator).trim().toLowerCase();
    const code = line.slice(separator + 1).trim();

    mapping.set(name, code);
  }

  return mapping;
}

export function parseReleaseDate(value) {
  if (!value) {
    return null;
  }

  const match = /^([A-Za-z]+) ([0-9]{1,2}), ([0-9]{4})(?: \[.*\])?$/u.exec(
    normalizeWhitespace(value),
  );

  if (!match) {
    return null;
  }

  const month = MONTHS.get(match[1].toLowerCase());
  const day = Number(match[2]);
  const year = Number(match[3]);

  if (!month || year < 1) {
    return null;
  }

  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysPerMonth = [
    31, leap ? 29 : 28, 31, 30, 31, 30,
    31, 31, 30, 31, 30, 31,
  ];

  if (day < 1 || day > daysPerMonth[month - 1]) {
    return null;
  }

  return [
    String(year).padStart(4, "0"),
    String(month).padStart(2, "0"),
    String(day).padStart(2, "0"),
  ].join("-");
}

export function parseHeader(header, {
  languageMap = loadLanguageMap(),
  warn = (message) => console.error(message),
} = {}) {
  const fields = new Map();
  let currentField = null;

  for (const line of header.split(/\r\n|\n|\r/u)) {
    // Only known field names at column zero start a new field.
    const match = FIELD.exec(line);

    if (match) {
      currentField = match[1].toLowerCase();
      fields.set(currentField, match[2]);
      continue;
    }

    if (currentField !== null && /^\s/u.test(line) && line.trim()) {
      fields.set(currentField, `${fields.get(currentField)} ${line.trim()}`);
      continue;
    }

    // Blank or unindented non-field lines terminate continuation.
    currentField = null;
  }

  for (const [name, value] of fields) {
    fields.set(name, normalizeWhitespace(value));
  }

  let title = fields.get("title");

  if (!title) {
    title = "Unknown";
    warn("MISSING_TITLE");
  }

  const rawAuthor = fields.get("author");
  const author = rawAuthor === undefined
    ? null
    : rawAuthor.replace(/,\s*[0-9]{4}\s*[-–]\s*[0-9]{4}\s*$/u, "").trim();

  const rawLanguage = fields.get("language");
  const language = rawLanguage === undefined
    ? null
    : rawLanguage.toLowerCase();

  return {
    title,
    author,
    language: language === null
      ? null
      : (languageMap.get(language) ?? language),
    release_date: parseReleaseDate(fields.get("release date")),
  };
}