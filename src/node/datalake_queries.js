import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { makeStorage } from "./ingestion.js";
import { ControlFiles } from "./control/files.js";

export function lookupBook({ workspace, layout = "time", bookId }) {
  const storage = makeStorage(workspace, layout);
  const paths = storage.lookup(bookId);

  if (!paths) return null;

  // Reading the body is part of the lookup operation required by SPEC §1.2.
  readFileSync(resolve(workspace, paths[1]));

  return paths;
}

export function scanNewBooks({
  workspace,
  layout = "time",
  since = new Date("0000-01-01T00:00:00Z"),
}) {
  const storage = makeStorage(workspace, layout);
  const indexed = new ControlFiles(workspace).indexedIds();

  return [...new Set(storage.listNew(since))]
    .filter((bookId) => !indexed.has(bookId))
    .sort((a, b) => a - b);
}