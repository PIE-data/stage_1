import { readFileSync } from "node:fs";

import { ControlFiles } from "./files.js";
import { withRunLock } from "./run_lock.js";
import { downloadBooksUnderLock } from "../ingestion.js";
import {
  IndexArgumentError,
  indexBooksUnderLock,
  readIndexConfig,
} from "../index_pipeline.js";

function positiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new IndexArgumentError(`${name} must be a positive safe integer`);
  }
}

function failedIds(path) {
  let text;

  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return new Set();
    throw error;
  }

  const result = new Set();

  for (const line of text.split(/\r\n|\n|\r/u)) {
    if (line.trim() === "") continue;

    const [id, reason, timestamp, extra] = line.split("\t");

    if (
      !/^[1-9][0-9]*$/u.test(id) ||
      !Number.isSafeInteger(Number(id)) ||
      !["NOT_FOUND", "NO_MARKERS", "DOWNLOAD_ERROR"].includes(reason) ||
      !Number.isFinite(Date.parse(timestamp)) ||
      extra !== undefined
    ) {
      throw new Error("Malformed failed_books.txt");
    }

    result.add(Number(id));
  }

  return result;
}

export async function controlStep({
  workspace,
  layout = "time",
  backend = "json",
  iterations,
  totalBooks = 70000,
  bookIds,
  sourceBase,
  now = new Date(),
  downloaderFactory,
}) {
  positiveInteger(iterations, "iterations");
  positiveInteger(totalBooks, "totalBooks");

  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new IndexArgumentError("A valid ingestion instant is required");
  }

  const candidates = bookIds === undefined ? null : [...new Set(bookIds)];

  if (candidates !== null) {
    for (const id of candidates) positiveInteger(id, "Manifest book ID");
  }

  return withRunLock(workspace, async () => {
    const control = new ControlFiles(workspace);
    const config = readIndexConfig(workspace, backend);
    const positions = config?.positions ?? true;

    let downloadedCount = 0;
    let indexedCount = 0;
    let failedCount = 0;
    let completed = 0;
    let cursor = 0;

    for (; completed < iterations; completed += 1) {
      const downloaded = control.downloadedIds();
      const indexed = control.indexedIds();
      const pending = [...downloaded]
        .filter((id) => !indexed.has(id))
        .sort((a, b) => a - b);

      if (pending.length > 0) {
        const result = await indexBooksUnderLock({
          workspace,
          layout,
          backend,
          bookId: pending[0],
          positions,
        });

        if (result.exitCode !== 0) {
          throw new Error(
            `Cannot index downloaded book ${pending[0]}; reconcile the workspace`,
          );
        }

        indexedCount += result.processed;
        continue;
      }

      const failed = failedIds(control.failedPath);
      let candidate;

      while (cursor < (candidates?.length ?? totalBooks)) {
        const id = candidates === null ? cursor + 1 : candidates[cursor];
        cursor += 1;

        if (!downloaded.has(id) && !failed.has(id)) {
          candidate = id;
          break;
        }
      }

      if (candidate === undefined) break;

      const code = await downloadBooksUnderLock({
        workspace,
        layout,
        bookIds: [candidate],
        now,
        workers: 1,
        sourceBase,
        downloaderFactory,
      });

      if (code === 0) downloadedCount += 1;
      else failedCount += 1;
    }

    return {
      exitCode: 0,
      iterations: completed,
      downloaded: downloadedCount,
      indexed: indexedCount,
      failed: failedCount,
    };
  });
}