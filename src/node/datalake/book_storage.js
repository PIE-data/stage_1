import { readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "./atomic.js";
import { buildMetadataRecord } from "../datamart/metadata_record.js";

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

export class BookStorage {
  constructor(workspace, { now = null } = {}) {
    this.workspace = resolve(workspace);
    this.root = join(this.workspace, "datalake", "books");

    if (
      now !== null &&
      (!(now instanceof Date) || !Number.isFinite(now.getTime()))
    ) {
      throw new TypeError("now must be a valid Date");
    }

    this.now = now === null ? null : new Date(now.getTime());
  }

  paths(bookId) {
    validateBookId(bookId);

    return [
      `datalake/books/${bookId}/header.txt`,
      `datalake/books/${bookId}/body.txt`,
    ];
  }

  write(bookId, header, body) {
    const paths = this.paths(bookId);

    // The pipeline supplies the ingestion instant explicitly.
    if (this.now === null) {
      throw new TypeError("An explicit ingestion time is required for write");
    }

    atomicWrite(join(this.workspace, paths[0]), header);
    atomicWrite(join(this.workspace, paths[1]), body);

    const record = buildMetadataRecord({
      bookId,
      workspace: this.workspace,
      headerPath: paths[0],
      bodyPath: paths[1],
      ingestedAt: this.now,
    });

    atomicWrite(
      join(this.root, String(bookId), "meta.json"),
      `${JSON.stringify(record)}\n`,
    );

    return paths;
  }

  lookup(bookId) {
    const paths = this.paths(bookId);

    // Resolve from the filesystem only; meta.json is not a lookup index.
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

    let directories;

    try {
      directories = readdirSync(this.root, { withFileTypes: true });
    } catch (error) {
      if (error.code === "ENOENT") {
        return [];
      }
      throw error;
    }

    const ids = [];

    for (const entry of directories) {
      if (!entry.isDirectory() || !/^[1-9][0-9]*$/u.test(entry.name)) {
        continue;
      }

      const bookId = Number(entry.name);
      if (!Number.isSafeInteger(bookId)) {
        continue;
      }

      const body = fileStat(join(this.root, entry.name, "body.txt"));

      if (body && body.mtimeMs >= since.getTime()) {
        ids.push(bookId);
      }
    }

    return ids.sort((a, b) => a - b);
  }
}