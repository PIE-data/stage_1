import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "../datalake/atomic.js";
import { buildPostings, canonicalJSON } from "./json_index.js";

export function termLocation(term) {
  if (typeof term !== "string" || term.length === 0) {
    throw new TypeError("Expected a non-empty term");
  }

  const first = [...term][0].toUpperCase();
  const bucket = /^[A-Z]$/u.test(first) ? first : "_";

  let filename = "";

  for (const byte of Buffer.from(term, "utf8")) {
    const plain =
      (byte >= 97 && byte <= 122) ||
      (byte >= 48 && byte <= 57);

    filename += plain
      ? String.fromCharCode(byte)
      : `%${byte.toString(16).toUpperCase().padStart(2, "0")}`;
  }

  return [bucket, `${filename}.txt`];
}

function entries(path) {
  try {
    return readdirSync(path, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

export class FolderIndex {
  constructor(workspace, { positions = false } = {}) {
    if (typeof positions !== "boolean") {
      throw new TypeError("positions must be a boolean");
    }

    this.root = join(resolve(workspace), "datamarts", "inverted_index");
    this.positions = positions;
  }

  pathFor(term) {
    return join(this.root, ...termLocation(term));
  }

  postings(term) {
    let text;

    try {
      text = readFileSync(this.pathFor(term), "utf8");
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }

    if (text === "") return [];

    if (!text.endsWith("\n")) {
      throw new Error("Posting files must end with LF");
    }

    let previousId = 0;

    return text.slice(0, -1).split("\n").map((line) => {
      const columns = line.split("\t");

      if (columns.length !== (this.positions ? 3 : 2)) {
        throw new Error("Invalid postings or incompatible positions setting");
      }

      const parseInteger = (value) => {
        if (!/^(0|[1-9][0-9]*)$/u.test(value)) {
          throw new Error("Invalid posting integer");
        }

        const number = Number(value);

        if (!Number.isSafeInteger(number)) {
          throw new Error("Posting integer exceeds the safe range");
        }

        return number;
      };

      const id = parseInteger(columns[0]);
      const tf = parseInteger(columns[1]);

      if (id <= previousId || tf < 1) {
        throw new Error("Postings require ascending book IDs and positive tf");
      }

      previousId = id;

      if (!this.positions) return [id, tf];

      const positions = columns[2].split(",").map(parseInteger);

      if (
        positions.length !== tf ||
        positions.some((position, index) =>
          index > 0 && position <= positions[index - 1])
      ) {
        throw new Error("Invalid posting positions");
      }

      return [id, tf, positions];
    });
  }

  writeBook(bookId, tokens) {
    const additions = buildPostings(bookId, tokens, this.positions);

    // Each term update reads and atomically rewrites only its own file.
    for (const [term, posting] of additions) {
      const postings = this.postings(term)
        .filter(([id]) => id !== bookId);

      postings.push(posting);
      postings.sort((left, right) => left[0] - right[0]);

      const text = postings.map(([id, tf, positions]) =>
        positions === undefined
          ? `${id}\t${tf}\n`
          : `${id}\t${tf}\t${positions.join(",")}\n`).join("");

      atomicWrite(this.pathFor(term), text);
    }
  }

  *allEntries() {
    for (const bucket of entries(this.root)) {
      if (!bucket.isDirectory() || !/^[A-Z_]$/u.test(bucket.name)) continue;

      for (const file of entries(join(this.root, bucket.name))) {
        if (!file.isFile() || !file.name.endsWith(".txt")) continue;

        const term = decodeURIComponent(file.name.slice(0, -4));
        const [expectedBucket, expectedName] = termLocation(term);

        if (bucket.name !== expectedBucket || file.name !== expectedName) {
          throw new Error(`Invalid term file location: ${bucket.name}/${file.name}`);
        }

        const postings = this.postings(term);
        if (postings.length > 0) yield [term, postings];
      }
    }
  }

  exportCanonical(outputPath) {
    atomicWrite(outputPath, canonicalJSON(this.allEntries()));
  }
}