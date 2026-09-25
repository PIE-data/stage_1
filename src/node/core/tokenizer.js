import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
export function normalizeText(text) {
  // Apply the normalization steps in the order required by SPEC.md §3.1.
  return text
    .normalize("NFKC")
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .normalize("NFC");
}
const DEFAULT_STOPWORDS_FILE = new URL(
  "../../../spec/stopwords_en.txt",
  import.meta.url
);

export function loadStopwords(path = DEFAULT_STOPWORDS_FILE) {
  // Read the shared list without modifying the spelling of its terms.
  const lines = readFileSync(path, "utf8").split(/\r?\n/u);
  const words = new Set();

  for (const line of lines) {
    const term = line.trim();

    if (term !== "" && !term.startsWith("#")) {
      words.add(term);
    }
  }

  return words;
}

export function extractRawTokens(normalizedText, isWordCharacter) {
  // Convert the string to Unicode code points before indexed access.
  const characters = [...normalizedText];
  const tokens = [];
  let buffer = "";

  function emitToken() {
    if (buffer !== "") {
      // Positions are assigned before any filtering.
      tokens.push({ term: buffer, position: tokens.length });
      buffer = "";
    }
  }

  for (let index = 0; index < characters.length; index += 1) {
    const character = characters[index];

    if (isWordCharacter(character)) {
      buffer += character;
      continue;
    }

    const isApostrophe = character === "'" || character === "\u2019";

    const isInternalJoiner =
      isApostrophe &&
      index > 0 &&
      index + 1 < characters.length &&
      isWordCharacter(characters[index - 1]) &&
      isWordCharacter(characters[index + 1]);

    if (isInternalJoiner) {
      buffer += character;
    } else {
      emitToken();
    }
  }

  // Emit the final token even when there is no trailing separator.
  emitToken();

  return tokens;
}

export function filterTokens(rawTokens, stopwords, isDigit) {
  return rawTokens.filter(({ term }) => {
    // Measure Unicode code points, not UTF-16 code units.
    const characters = [...term];

    if (characters.length < 2) {
      return false;
    }

    if (characters.length > 40) {
      return false;
    }

    if (characters.every(isDigit)) {
      return false;
    }

    if (stopwords.has(term)) {
      return false;
    }

    // Keep the original token and its position unchanged.
    return true;
  });
}

export function tokenize(text, stopwords, { isWordCharacter, isDigit }) {
  const normalizedText = normalizeText(text);
  const rawTokens = extractRawTokens(normalizedText, isWordCharacter);
  const tokens = filterTokens(rawTokens, stopwords, isDigit);

  // Hash kept terms in document order, separated by LF with no trailing LF.
  const serializedTokens = tokens.map(({ term }) => term).join("\n");
  const checksum = createHash("sha256")
    .update(serializedTokens, "utf8")
    .digest("hex");

  return {
    tokens,
    n_tokens_raw: rawTokens.length,
    n_tokens_kept: tokens.length,
    sha256_tokens: checksum,
  };
}