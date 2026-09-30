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

// Internal operation: the caller must hold the workspace lock.
export async function indexBooksUnderLock({
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

  return (async () => {
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
        const resolved = [];

        // Resolve the whole batch before changing its index artifacts.
        for (const id of batchIds) {
          const paths = storage.lookup(id);

          if (paths === null) {
            return { exitCode: 3, processed };
          }

          resolved.push([id, join(root, paths[1])]);
        }

        // Read and tokenize lazily, one book at a time: holding the token
        // streams of a whole 500-book batch takes gigabytes (1 000-book tier).
        const books = (function* () {
          for (const [bookId, bodyPath] of resolved) {
            const body = readFileSync(bodyPath, "utf8");
            yield { bookId, tokens: tokenize(body, stopwords).tokens };
          }
        })();

        if (index === undefined) {
          if (config === null) {
            atomicWrite(
              configPath(root, backend),
              `${JSON.stringify({ backend, positions })}\n`,
            );
          }

          index = await openIndex(root, backend, positions);
        }

        try {
          if (backend === "sqlite" || backend === "json") {
            // One load-merge-rewrite per batch for json (SPEC §6.1), one
            // transaction per batch for sqlite (§6.3); either is all or nothing.
            index.writeBatch(books);
          } else {
            // File backends publish each required artifact atomically.
            for (const book of books) {
              index.writeBook(book.bookId, book.tokens);
            }
          }
        } catch (error) {
          if (error.code === "ENOENT") {
            return { exitCode: 3, processed };
          }
          throw error;
        }

        control.markIndexedBatch(batchIds);
        processed += resolved.length;
      }

      return { exitCode: 0, processed };
    } finally {
      index?.close?.();
    }
  })();
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
  const { writeCanonical } = await import("./index/json_index.js");
  const db = new DatabaseSync(artifactPath(root, backend), { readOnly: true });

  try {
    // Term by term, never all postings at once: 1 000 books with positions
    // are millions of rows, too many to materialise in one array.
    const terms = db.prepare("SELECT DISTINCT term FROM postings")
      .all().map(({ term }) => term);
    const select = db.prepare(`
      SELECT book_id, tf, positions FROM postings
      WHERE term = ? ORDER BY book_id
    `);

    writeCanonical(out, terms, (term) => select.all(term).map((row) => {
      if ((row.positions !== null) !== config.positions) {
        throw new Error("Index positions do not match its configuration");
      }

      return row.positions === null
        ? [row.book_id, row.tf]
        : [row.book_id, row.tf, row.positions.split(",").map(Number)];
    }));
    return 0;
  } finally {
    db.close();
  }
}

export async function indexBooks(options) {
  return withRunLock(options.workspace, () => indexBooksUnderLock(options));
}