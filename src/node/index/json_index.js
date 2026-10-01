import { closeSync, existsSync, openSync, readSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWriteChunks } from "../datalake/atomic.js";

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

/**
 * Stream the canonical export (SPEC §7) term by term, for indexes too large to
 * serialise as one string.  `terms` in any order; `postingsOf(term)` returns
 * that term's postings as arrays.  Same bytes as canonicalJSON().
 */
export function writeCanonical(outputPath, terms, postingsOf) {
  const keyed = [...terms]
    .map((term) => [Buffer.from(term, "utf8"), term])
    .sort((left, right) => Buffer.compare(left[0], right[0]));

  atomicWriteChunks(outputPath, (write) => {
    write("{");
    let first = true;
    for (const [, term] of keyed) {
      const ordered = postingsOf(term)
        .map(([id, tf, positions]) =>
          positions === undefined
            ? [id, tf]
            : [id, tf, [...positions].sort((a, b) => a - b)])
        .sort((left, right) => left[0] - right[0]);
      if (ordered.length === 0) continue;
      write(`${first ? "" : ","}${JSON.stringify(term)}:${JSON.stringify({
        df: ordered.length,
        postings: ordered,
      })}`);
      first = false;
    }
    write("}");
  });
}

// ---------------------------------------------------------------------------
// The file is written in exactly the canonical form of SPEC §7 -- the same
// bytes Python writes -- so export-canonical is a re-serialisation:
//
//     {"<term>":{"df":<n>,"postings":[[<id>,<tf>,[<pos>,...]],...]},...}
//
// It can exceed V8's ~512M-character string limit (1 000 books with positions
// do), so it is never held as one string: it is read and written one term at a
// time, and in memory each posting is kept as its own serialised text rather
// than as arrays of numbers.  None of this changes the load-merge-rewrite work
// SPEC §6.1 prescribes; `index --all` merges a whole --batch-size batch per
// rewrite, as the Python reference does.
// ---------------------------------------------------------------------------

const READ_CHUNK = 8 * 1024 * 1024;

/** The book id of a serialised posting "[<id>,<tf>...": the text up to the first comma. */
export function idOf(text) {
  return Number(text.slice(1, text.indexOf(",")));
}

function postingText(posting) {
  return posting.length === 3
    ? `[${posting[0]},${posting[1]},[${posting[2].join(",")}]]`
    : `[${posting[0]},${posting[1]}]`;
}

/** Insert or replace `text` (book `bookId`) in `texts`, kept ascending by id. */
function upsert(texts, bookId, text) {
  const last = texts.length - 1;
  if (last < 0 || idOf(texts[last]) < bookId) {
    texts.push(text); // the common case: `index --all` goes in ascending id order
    return;
  }
  let lo = 0;
  let hi = last;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const id = idOf(texts[mid]);
    if (id === bookId) {
      texts[mid] = text;
      return;
    }
    if (id < bookId) lo = mid + 1;
    else hi = mid - 1;
  }
  texts.splice(lo, 0, text);
}

export class JsonIndex {
  constructor(workspace, { positions = false } = {}) {
    if (typeof positions !== "boolean") {
      throw new TypeError("positions must be a boolean");
    }

    this.path = join(resolve(workspace), "datamarts", "inverted_index.json");
    this.positions = positions;
  }

  /**
   * Parse the file one term entry at a time.  Returns
   * { entries: Map<term, postingText[]> ascending by id, books: Set<id> }.
   */
  load() {
    const entries = new Map();
    const books = new Set();
    if (!existsSync(this.path)) return { entries, books };

    const width = this.positions ? 3 : 2;
    const addEntry = (parts) => {
      const text = Buffer.concat(parts).toString("utf8");
      const value = JSON.parse(`{${text}}`);
      const [term] = Object.keys(value);
      const { df, postings } = value[term] ?? {};
      if (!Array.isArray(postings) || df !== postings.length) {
        throw new Error("Invalid JSON index");
      }
      const texts = [];
      for (const posting of postings) {
        if (!Array.isArray(posting) || posting.length !== width) {
          throw new Error("Invalid postings or incompatible positions setting");
        }
        books.add(posting[0]);
        texts.push(postingText(posting));
      }
      entries.set(term, texts);
    };

    // Terms hold only letters, digits and apostrophes (SPEC §3), never
    // brackets, braces or commas: nesting depth alone delimits the entries.
    const fd = openSync(this.path, "r");
    try {
      let depth = 0;
      let parts = [];
      let done = false;
      while (!done) {
        const buffer = Buffer.allocUnsafe(READ_CHUNK);
        const n = readSync(fd, buffer, 0, READ_CHUNK, null);
        if (n === 0) break;
        let start = 0;
        for (let i = 0; i < n; i += 1) {
          const c = buffer[i];
          if (c === 0x7b || c === 0x5b) { // { [
            depth += 1;
            if (depth === 1) start = i + 1;
          } else if (c === 0x7d || c === 0x5d) { // } ]
            depth -= 1;
            if (depth === 1 && c === 0x7d) {
              parts.push(buffer.subarray(start, i + 1));
              addEntry(parts);
              parts = [];
              start = i + 1;
            } else if (depth === 0) {
              done = true;
              break;
            }
          } else if (c === 0x2c && depth === 1) { // , between entries
            start = i + 1;
          }
        }
        if (!done && depth >= 1 && start < n) parts.push(buffer.subarray(start, n));
      }
      if (depth !== 0) throw new Error("Invalid JSON index");
    } finally {
      closeSync(fd);
    }
    return { entries, books };
  }

  /** Term -> postings as arrays.  Materialises everything: small indexes and tests only. */
  read() {
    const { entries } = this.load();
    return new Map([...entries].map(([term, texts]) =>
      [term, texts.map((text) => JSON.parse(text))]));
  }

  writeBook(bookId, tokens) {
    this.writeBatch([{ bookId, tokens }]);
  }

  /**
   * Load once, merge every book of the batch, rewrite once (SPEC §6.1).  A
   * single book is a batch of one: it still pays the whole load and rewrite,
   * which is what experiment E8 measures.
   */
  writeBatch(books) {
    // `books` may be a lazy iterable: each book is folded into the index and
    // its tokens dropped before the next is read, so a batch never holds
    // 500 books' token streams at once.  Nothing is written until the whole
    // batch has been validated and merged, so an error leaves the file as it was.
    const { entries, books: present } = this.load();

    for (const { bookId, tokens } of books) {
      const postings = buildPostings(bookId, tokens, this.positions);
      // Re-indexing a book already present: drop its old postings, including
      // terms its new content no longer has.  Only then, so the common case
      // (a new book) never scans the whole index.
      if (present.has(bookId)) {
        for (const [term, texts] of entries) {
          const kept = texts.filter((text) => idOf(text) !== bookId);
          if (kept.length === 0) entries.delete(term);
          else if (kept.length !== texts.length) entries.set(term, kept);
        }
      }
      for (const [term, posting] of postings) {
        const texts = entries.get(term);
        if (texts === undefined) entries.set(term, [postingText(posting)]);
        else upsert(texts, bookId, postingText(posting));
      }
      present.add(bookId);
    }

    this.#write(this.path, entries);
  }

  #write(path, entries) {
    const keyed = [...entries.keys()]
      .map((term) => [Buffer.from(term, "utf8"), term])
      .sort((left, right) => Buffer.compare(left[0], right[0]));

    atomicWriteChunks(path, (write) => {
      write("{");
      let first = true;
      for (const [, term] of keyed) {
        const texts = entries.get(term);
        write(`${first ? "" : ","}${JSON.stringify(term)}:{"df":${texts.length},` +
          `"postings":[${texts.join(",")}]}`);
        first = false;
      }
      write("}");
    });
  }

  postings(term) {
    const texts = this.load().entries.get(term) ?? [];
    return texts.map((text) => JSON.parse(text));
  }

  exportCanonical(outputPath) {
    // The file already has the canonical shape; rewriting it from the parsed
    // entries also re-validates it.
    this.#write(outputPath, this.load().entries);
  }
}
