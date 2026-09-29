import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { ControlFiles } from "./control/files.js";
import { withRunLock } from "./control/run_lock.js";
import { atomicWrite } from "./datalake/atomic.js";
import { makeStorage } from "./ingestion.js";
import { loadStopwords, tokenize } from "./core/tokenizer.js";

export class IndexArgumentError extends Error {
  constructor(message) {
    super(message);
    this.name = "IndexArgumentError";
  }
}

function configPath(workspace, backend) {
  return join(workspace, "datamarts", `${backend}.index-config.json`);
}

function artifactPath(workspace, backend) {
  const names = {
    json: "inverted_index.json",
    folder: "inverted_index",
    sqlite: "index.db",
  };
  return join(workspace, "datamarts", names[backend]);
}

export function readIndexConfig(workspace, backend) {
  if (!["json", "folder", "sqlite"].includes(backend)) {
    throw new IndexArgumentError("Unsupported index backend");
  }

  let text;

  try {
    text = readFileSync(configPath(workspace, backend), "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;

    if (existsSync(artifactPath(workspace, backend))) {
      throw new Error("Existing index has no positions configuration");
    }

    return null;
  }

  const config = JSON.parse(text);

  if (
    config === null ||
    config.backend !== backend ||
    typeof config.positions !== "boolean"
  ) {
    throw new Error("Invalid index configuration");
  }

  return config;
}

export async function openIndex(workspace, backend, positions) {
  if (backend === "json") {
    const { JsonIndex } = await import("./index/json_index.js");
    return new JsonIndex(workspace, { positions });
  }

  if (backend === "folder") {
    const { FolderIndex } = await import("./index/folder_index.js");
    return new FolderIndex(workspace, { positions });
  }

  if (backend === "sqlite") {
    const { SqliteIndex } = await import("./index/sqlite_index.js");
    return new SqliteIndex(workspace, { positions });
  }

  throw new IndexArgumentError("Unsupported index backend");
}

export async function indexBooks({
  workspace,
  layout = "time",
  backend = "json",
  bookId,
  all = false,
  positions = false,
  batchSize = 500,
}) {
  if ((bookId !== undefined) === all) {
    throw new IndexArgumentError("Choose exactly one of bookId or all");
  }

  if (
    bookId !== undefined &&
    (!Number.isSafeInteger(bookId) || bookId <= 0)
  ) {
    throw new IndexArgumentError("bookId must be a positive safe integer");
  }

  if (!Number.isSafeInteger(batchSize) || batchSize <= 0) {
    throw new IndexArgumentError("batchSize must be a positive safe integer");
  }

  if (typeof positions !== "boolean") {
    throw new IndexArgumentError("positions must be a boolean");
  }

  if (!["json", "folder", "sqlite"].includes(backend)) {
    throw new IndexArgumentError("Unsupported index backend");
  }

  const root = resolve(workspace);

  return withRunLock(root, async () => {
    const config = readIndexConfig(root, backend);

    if (config !== null && config.positions !== positions) {
      throw new IndexArgumentError(
        "--positions must match the setting used to build this index",
      );
    }

    const control = new ControlFiles(root);
    const indexed = control.indexedIds();
    const ids = all
      ? [...control.downloadedIds()]
        .filter((id) => !indexed.has(id))
        .sort((a, b) => a - b)
      : [bookId];

    if (ids.length === 0) return { exitCode: 0, processed: 0 };

    const storage = makeStorage(root, layout);
    const stopwords = loadStopwords();
    let index;
    let processed = 0;

    try {
      for (let offset = 0; offset < ids.length; offset += batchSize) {
        const batchIds = ids.slice(offset, offset + batchSize);
        const books = [];

        // Resolve and tokenize the batch before changing its index artifacts.
        for (const id of batchIds) {
          const paths = storage.lookup(id);

          if (paths === null) {
            return { exitCode: 3, processed };
          }

          let body;

          try {
            body = readFileSync(join(root, paths[1]), "utf8");
          } catch (error) {
            if (error.code === "ENOENT") {
              return { exitCode: 3, processed };
            }
            throw error;
          }

          books.push({
            bookId: id,
            tokens: tokenize(body, stopwords).tokens,
          });
        }

        if (index === undefined) {
          if (config === null) {
            atomicWrite(
              configPath(root, backend),
              `${JSON.stringify({ backend, positions })}\n`,
            );
          }

          index = await openIndex(root, backend, positions);
        }

        if (backend === "sqlite") {
          index.writeBatch(books);
        } else {
          // File backends publish each required artifact atomically.
          for (const book of books) {
            index.writeBook(book.bookId, book.tokens);
          }
        }

        control.markIndexedBatch(batchIds);
        processed += books.length;
      }

      return { exitCode: 0, processed };
    } finally {
      index?.close?.();
    }
  });
}

export async function exportIndex({
  workspace,
  backend = "json",
  out,
}) {
  const root = resolve(workspace);
  const config = readIndexConfig(root, backend);

  if (config === null) {
    throw new Error("No index configuration found for the selected backend");
  }

  if (backend !== "sqlite") {
    const index = await openIndex(root, backend, config.positions);
    index.exportCanonical(out);
    return 0;
  }

  const { DatabaseSync } = await import("node:sqlite");
  const { canonicalJSON } = await import("./index/json_index.js");
  const db = new DatabaseSync(artifactPath(root, backend), { readOnly: true });

  try {
    const entries = new Map();

    const rows = db.prepare(`
      SELECT term, book_id, tf, positions
      FROM postings ORDER BY term, book_id
    `).all();

    for (const row of rows) {
      if ((row.positions !== null) !== config.positions) {
        throw new Error("Index positions do not match its configuration");
      }

      const posting = row.positions === null
        ? [row.book_id, row.tf]
        : [row.book_id, row.tf, row.positions.split(",").map(Number)];

      if (!entries.has(row.term)) entries.set(row.term, []);
      entries.get(row.term).push(posting);
    }

    atomicWrite(out, canonicalJSON(entries));
    return 0;
  } finally {
    db.close();
  }
}