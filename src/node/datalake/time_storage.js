import { readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "./atomic.js";

function validateBookId(bookId) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new TypeError("bookId must be a positive safe integer");
  }
}

function validateDate(value) {
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
    throw new TypeError("Expected a valid Date");
  }

  const year = value.getUTCFullYear();
  if (year < 0 || year > 9999) {
    throw new RangeError("Year must fit the YYYY directory format");
  }
}

function dateParts(value) {
  validateDate(value);

  const year = String(value.getUTCFullYear()).padStart(4, "0");
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  const hour = String(value.getUTCHours()).padStart(2, "0");

  return { date: `${year}${month}${day}`, hour };
}

function entries(directory) {
  try {
    return readdirSync(directory, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

function isFile(path) {
  try {
    return statSync(path).isFile();
  } catch (error) {
    if (error.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

export class TimeStorage {
  constructor(workspace, { now = null } = {}) {
    this.workspace = resolve(workspace);
    this.root = join(this.workspace, "datalake");

    if (now !== null) {
      validateDate(now);
    }

    // Copy the override so callers cannot mutate it after construction.
    this.now = now === null ? null : new Date(now.getTime());
  }

  paths(bookId, date, hour) {
    return [
      `datalake/${date}/${hour}/${bookId}.header.txt`,
      `datalake/${date}/${hour}/${bookId}.body.txt`,
    ];
  }

  write(bookId, header, body) {
    validateBookId(bookId);

    const { date, hour } = dateParts(this.now ?? new Date());
    const paths = this.paths(bookId, date, hour);

    atomicWrite(join(this.workspace, paths[0]), header);
    atomicWrite(join(this.workspace, paths[1]), body);

    return paths;
  }

  *buckets(since = null) {
    const dates = entries(this.root)
      .filter((entry) =>
        entry.isDirectory() && /^[0-9]{8}$/u.test(entry.name))
      .map((entry) => entry.name)
      .sort();

    for (const date of dates) {
      if (since !== null && date < since.date) {
        continue;
      }

      const hours = entries(join(this.root, date))
        .filter((entry) =>
          entry.isDirectory() && /^(0[0-9]|1[0-9]|2[0-3])$/u.test(entry.name))
        .map((entry) => entry.name)
        .sort();

      for (const hour of hours) {
        if (since !== null && date === since.date && hour < since.hour) {
          continue;
        }

        yield { date, hour };
      }
    }
  }

    lookup(bookId) {
    validateBookId(bookId);

    let latest = null;

    // Buckets are ordered chronologically. Keep the newest complete pair.
    // Resolve from the filesystem on every call, without auxiliary metadata.
    for (const { date, hour } of this.buckets()) {
      const paths = this.paths(bookId, date, hour);

      if (
        isFile(join(this.workspace, paths[0])) &&
        isFile(join(this.workspace, paths[1]))
      ) {
        latest = paths;
      }
    }

    return latest;
  }

  listNew(since) {
    const boundary = dateParts(since);
    const ids = new Set();

    // SPEC §4.1 uses hour buckets, not per-file mtime, for this layout.
    for (const { date, hour } of this.buckets(boundary)) {
      const directory = join(this.root, date, hour);

      for (const entry of entries(directory)) {
        if (!entry.isFile()) {
          continue;
        }

        const match = /^([1-9][0-9]*)\.body\.txt$/u.exec(entry.name);
        if (!match) {
          continue;
        }

        const bookId = Number(match[1]);
        if (Number.isSafeInteger(bookId)) {
          ids.add(bookId);
        }
      }
    }

    return [...ids].sort((a, b) => a - b);
  }
}