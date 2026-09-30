import { readdirSync, unlinkSync } from "node:fs";
import { join, resolve } from "node:path";

import { atomicWrite } from "../datalake/atomic.js";
import { makeStorage } from "../ingestion.js";
import { ControlFiles } from "./files.js";
import { withRunLock } from "./run_lock.js";

function findPartialFiles(directory) {
  const paths = [];

  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);

    // Never follow symbolic links outside the workspace.
    if (entry.isSymbolicLink()) continue;

    if (entry.isDirectory()) {
      paths.push(...findPartialFiles(path));
    } else if (entry.isFile() && entry.name.endsWith(".part")) {
      paths.push(path);
    }
  }

  return paths.sort();
}

function serializeIds(ids) {
  return ids.length === 0 ? "" : `${ids.join("\n")}\n`;
}

export async function reconcileWorkspace({
  workspace,
  layout = "time",
}) {
  const root = resolve(workspace);
  const storage = makeStorage(root, layout);

  return withRunLock(root, async () => {
    const control = new ControlFiles(root);
    const indexed = control.indexedIds();

    const candidates = storage.listNew(
      new Date("0000-01-01T00:00:00Z"),
    );

    // Only complete header/body pairs qualify as downloaded.
    const downloaded = [...new Set(candidates)]
      .filter((bookId) => storage.lookup(bookId) !== null)
      .sort((a, b) => a - b);

    const retainedIndexed = downloaded.filter((id) => indexed.has(id));
    const partialFiles = findPartialFiles(root);

    // The workspace lock excludes cooperating writers during cleanup.
    // Remove stale control-file partials before atomic replacement.
    for (const path of partialFiles) {
      unlinkSync(path);
    }

    atomicWrite(
      join(root, "control", "downloaded_books.txt"),
      serializeIds(downloaded),
    );

    atomicWrite(
      join(root, "control", "indexed_books.txt"),
      serializeIds(retainedIndexed),
    );

    // Reconciliation does not invent metadata or ingestion timestamps.
    return {
      downloaded: downloaded.length,
      indexed: retainedIndexed.length,
      partialsRemoved: partialFiles.length,
    };
  });
}