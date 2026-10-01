import { readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "./atomic.js";

function validateBookId(bookId) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new TypeError("bookId must be a positive safe integer");
  }
}

function fileStat(path) {
  try {
    const result = statSync(path);
    return result.isFile() ? result : null;
  } catch (error) {
    if (error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

function directoryEntries(path) {
  try {
    return readdirSync(path, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

export class HashStorage {
  constructor(workspace) {
    this.workspace = resolve(workspace);
    this.root = join(this.workspace, "datalake");
  }

  paths(bookId) {
    validateBookId(bookId);

    // Decimal digit prefixes, not a hash function.
    const id6 = String(bookId).padStart(6, "0");
    const directory = `datalake/${id6.slice(0, 2)}/${id6.slice(2, 4)}`;

    return [
      `${directory}/${bookId}.header.txt`,
      `${directory}/${bookId}.body.txt`,
    ];
  }

  write(bookId, header, body) {
    const paths = this.paths(bookId);

    atomicWrite(join(this.workspace, paths[0]), header);
    atomicWrite(join(this.workspace, paths[1]), body);

    return paths;
  }

  lookup(bookId) {
    const paths = this.paths(bookId);

    // Resolve directly from layout rules; never consult metadata or a cache.
    if (
      fileStat(join(this.workspace, paths[0])) &&
      fileStat(join(this.workspace, paths[1]))
    ) {
      return paths;
    }

    return null;
  }

  listNew(since) {
    if (!(since instanceof Date) || !Number.isFinite(since.getTime())) {
      throw new TypeError("since must be a valid Date");
    }

    const ids = [];

    // Visit every hash bucket, regardless of the requested timestamp.
    for (const first of directoryEntries(this.root)) {
      if (!first.isDirectory() || !/^[0-9]{2}$/u.test(first.name)) {
        continue;
      }

      const firstPath = join(this.root, first.name);

      for (const second of directoryEntries(firstPath)) {
        if (!second.isDirectory() || !/^[0-9]{2}$/u.test(second.name)) {
          continue;
        }

        const secondPath = join(firstPath, second.name);

        for (const entry of directoryEntries(secondPath)) {
          if (!entry.isFile()) {
            continue;
          }

          const match = /^([1-9][0-9]*)\.body\.txt$/u.exec(entry.name);
          if (!match) {
            continue;
          }

          const bookId = Number(match[1]);
          if (!Number.isSafeInteger(bookId)) {
            continue;
          }

          const paths = this.paths(bookId);
          const relativeBody =
            `datalake/${first.name}/${second.name}/${entry.name}`;

          // Ignore files placed outside their prescribed bucket.
          if (paths[1] !== relativeBody) {
            continue;
          }

          const bodyStat = fileStat(join(this.workspace, paths[1]));

          if (bodyStat && bodyStat.mtimeMs >= since.getTime()) {
            ids.push(bookId);
          }
        }
      }
    }

    return ids.sort((a, b) => a - b);
  }
}