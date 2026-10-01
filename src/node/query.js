import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

import { loadStopwords, tokenize } from "./core/tokenizer.js";
import { IndexArgumentError, readIndexConfig } from "./index_pipeline.js";

export async function queryIndex({
  workspace,
  backend = "json",
  terms,
  mode,
  limit,
}) {
  if (typeof terms !== "string") {
    throw new IndexArgumentError("--terms must be supplied");
  }

  if (!["and", "or"].includes(mode)) {
    throw new IndexArgumentError("--mode must be and or or");
  }

  if (
    limit !== undefined &&
    (!Number.isSafeInteger(limit) || limit < 0)
  ) {
    throw new IndexArgumentError("--limit must be a non-negative safe integer");
  }

  const root = resolve(workspace);
  const config = readIndexConfig(root, backend);

  if (config === null) {
    throw new Error("No index exists for the selected backend");
  }

  const names = {
    json: "inverted_index.json",
    folder: "inverted_index",
    sqlite: "index.db",
  };
  const path = join(root, "datamarts", names[backend]);

  if (!existsSync(path)) {
    throw new Error("Index artifact is missing for the selected backend");
  }

  const normalized = tokenize(terms, loadStopwords()).tokens;
  const unique = [...new Set(normalized.map(({ term }) => term))];

  if (backend === "sqlite") {
    const { DatabaseSync } = await import("node:sqlite");
    const db = new DatabaseSync(path, { readOnly: true });

    try {
      // Check the schema even for an empty query.
      db.prepare("SELECT book_id, tf FROM postings LIMIT 0").all();

      if (unique.length === 0 || limit === 0) return [];

      const placeholders = unique.map(() => "?").join(",");
      const sql = mode === "and"
        ? `SELECT book_id FROM postings
           WHERE term IN (${placeholders})
           GROUP BY book_id HAVING COUNT(*) = ?
           ORDER BY book_id`
        : `SELECT DISTINCT book_id FROM postings
           WHERE term IN (${placeholders})
           ORDER BY book_id`;

      const parameters = mode === "and"
        ? [...unique, unique.length]
        : unique;

      const ids = db.prepare(sql).all(...parameters)
        .map(({ book_id }) => book_id);

      return limit === undefined ? ids : ids.slice(0, limit);
    } finally {
      db.close();
    }
  }

  let getIds;

  if (backend === "json") {
    const { JsonIndex, idOf } = await import("./index/json_index.js");
    // Load the monolithic file once per query.
    const { entries } = new JsonIndex(root, config).load();
    getIds = (term) => (entries.get(term) ?? []).map(idOf);
  } else {
    const { FolderIndex } = await import("./index/folder_index.js");
    const index = new FolderIndex(root, config);
    getIds = (term) => index.postings(term).map(([id]) => id);
  }

  if (unique.length === 0 || limit === 0) return [];

  let matches = new Set(getIds(unique[0]));

  for (const term of unique.slice(1)) {
    const ids = new Set(getIds(term));

    if (mode === "and") {
      matches = new Set([...matches].filter((id) => ids.has(id)));
    } else {
      for (const id of ids) matches.add(id);
    }
  }

  const ordered = [...matches].sort((a, b) => a - b);
  return limit === undefined ? ordered : ordered.slice(0, limit);
}