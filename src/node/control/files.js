import {
  appendFileSync,
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";

function validateBookId(bookId) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new RangeError("bookId must be a positive safe integer");
  }
}

function appendDurably(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  const fd = openSync(path, "a");

  try {
    appendFileSync(fd, text, { encoding: "utf8" });
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

export function readBookIds(path) {
  let text;

  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return new Set();
    throw error;
  }

  const ids = new Set();

  for (const line of text.split(/\r\n|\n|\r/u)) {
    const value = line.trim();
    if (value === "") continue;

    if (!/^[1-9][0-9]*$/u.test(value)) {
      throw new Error(`Invalid book ID in ${path}: ${value}`);
    }

    const bookId = Number(value);
    validateBookId(bookId);
    ids.add(bookId);
  }

  return ids;
}

export class ControlFiles {
  constructor(workspace) {
    this.root = resolve(workspace, "control");
    this.downloadedPath = resolve(this.root, "downloaded_books.txt");
    this.indexedPath = resolve(this.root, "indexed_books.txt");
    this.failedPath = resolve(this.root, "failed_books.txt");
  }

  downloadedIds() {
    return readBookIds(this.downloadedPath);
  }

  indexedIds() {
    return readBookIds(this.indexedPath);
  }

  // The caller must hold the workspace lock and finish all artifacts first.
  markDownloaded(bookId) {
    validateBookId(bookId);

    if (this.downloadedIds().has(bookId)) return false;

    appendDurably(this.downloadedPath, `${bookId}\n`);
    return true;
  }

  recordFailure(bookId, reason, instant) {
    validateBookId(bookId);

    const reasons = new Set([
      "NOT_FOUND",
      "NO_MARKERS",
      "DOWNLOAD_ERROR",
    ]);

    if (!reasons.has(reason)) {
      throw new RangeError(`Invalid failure reason: ${reason}`);
    }

    if (!(instant instanceof Date) || !Number.isFinite(instant.getTime())) {
      throw new TypeError("A valid failure instant must be supplied");
    }

    const timestamp = instant.toISOString().replace(".000Z", "Z");

    appendDurably(
      this.failedPath,
      `${bookId}\t${reason}\t${timestamp}\n`,
    );
  }
}