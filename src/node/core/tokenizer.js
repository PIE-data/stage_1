import { readFileSync } from "node:fs";
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