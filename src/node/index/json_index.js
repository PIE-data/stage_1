import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "../datalake/atomic.js";

export function compareTerms(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

export function buildPostings(bookId, tokens, positions) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new TypeError("bookId must be a positive safe integer");
  }

  const grouped = new Map();
  let previousPosition = -1;

  for (const { term, position } of tokens) {
    if (
      typeof term !== "string" ||
      term.length === 0 ||
      !Number.isSafeInteger(position) ||
      position < 0 ||
      position <= previousPosition
    ) {
      throw new TypeError("Tokens must have strictly increasing valid positions");
    }

    previousPosition = position;

    if (!grouped.has(term)) {
      grouped.set(term, []);
    }

    grouped.get(term).push(position);
  }

  return new Map(
    [...grouped].map(([term, occurrences]) => [
      term,
      positions
        ? [bookId, occurrences.length, occurrences]
        : [bookId, occurrences.length],
    ]),
  );
}

// Serialize entries explicitly: object property enumeration can reorder numeric keys.
export function canonicalJSON(entries) {
  const sorted = [...entries].sort(([left], [right]) =>
    compareTerms(left, right));

  return `{${sorted.map(([term, postings]) => {
    const ordered = postings
      .map(([id, tf, positions]) =>
        positions === undefined
          ? [id, tf]
          : [id, tf, [...positions].sort((a, b) => a - b)])
      .sort((left, right) => left[0] - right[0]);

    return `${JSON.stringify(term)}:${JSON.stringify({
      df: ordered.length,
      postings: ordered,
    })}`;
  }).join(",")}}`;
}

export class JsonIndex {
  constructor(workspace, { positions = false } = {}) {
    if (typeof positions !== "boolean") {
      throw new TypeError("positions must be a boolean");
    }

    this.path = join(resolve(workspace), "datamarts", "inverted_index.json");
    this.positions = positions;
  }

  read() {
    let text;

    try {
      text = readFileSync(this.path, "utf8");
    } catch (error) {
      if (error.code === "ENOENT") return new Map();
      throw error;
    }

    const value = JSON.parse(text);

    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Invalid JSON index");
    }

    const entries = new Map(Object.entries(value));

    for (const postings of entries.values()) {
      if (
        !Array.isArray(postings) ||
        postings.some((posting) =>
          !Array.isArray(posting) ||
          posting.length !== (this.positions ? 3 : 2))
      ) {
        throw new Error("Invalid postings or incompatible positions setting");
      }
    }

    return entries;
  }

  writeBook(bookId, tokens) {
    // Validate all input before reading or modifying persistent state.
    const additions = buildPostings(bookId, tokens, this.positions);
    const index = this.read();

    // Replace this book's postings, including terms removed from its new content.
    for (const [term, postings] of index) {
      const retained = postings.filter(([id]) => id !== bookId);

      if (retained.length === 0) {
        index.delete(term);
      } else {
        index.set(term, retained);
      }
    }

    for (const [term, posting] of additions) {
      const postings = index.get(term) ?? [];
      postings.push(posting);
      postings.sort((left, right) => left[0] - right[0]);
      index.set(term, postings);
    }

    // SPEC §6.1 requires a full read/merge/rewrite for each book.
    const entries = [...index].sort(([left], [right]) =>
      compareTerms(left, right));

    const serialized = `{${entries.map(([term, postings]) =>
      `${JSON.stringify(term)}:${JSON.stringify(postings)}`).join(",")}}\n`;

    atomicWrite(this.path, serialized);
  }

  postings(term) {
    return this.read().get(term) ?? [];
  }

  exportCanonical(outputPath) {
    const serialized = canonicalJSON(this.read());
    atomicWrite(outputPath, serialized);
  }
}