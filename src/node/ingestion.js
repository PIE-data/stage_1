import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { splitText, MarkersNotFound } from "./datalake/splitter.js";
import { BookStorage } from "./datalake/book_storage.js";
import { HashStorage } from "./datalake/hash_storage.js";
import { TimeStorage } from "./datalake/time_storage.js";
import { ControlFiles } from "./control/files.js";
import { withRunLock } from "./control/run_lock.js";
import { atomicWrite } from "./datalake/atomic.js";
import {
  createDownloader,
  DownloadError,
} from "./datalake/downloader.js";

export function makeStorage(workspace, layout, now) {
  switch (layout) {
    case "book":
      return new BookStorage(workspace, { now });
    case "hash":
      return new HashStorage(workspace);
    case "time":
      return new TimeStorage(workspace, { now });
    default:
      throw new RangeError(`Unknown datalake layout: ${layout}`);
  }
}

function validateInputs(bookId, now) {
  if (!Number.isSafeInteger(bookId) || bookId <= 0) {
    throw new RangeError("bookId must be a positive safe integer");
  }

  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new TypeError("A valid ingestion instant must be supplied");
  }
}

// The caller must hold the workspace lock.
export function ingestText({
  workspace,
  bookId,
  text,
  storage,
  now,
}) {
  validateInputs(bookId, now);
  const control = new ControlFiles(workspace);

  let parts;
  try {
    parts = splitText(text);
  } catch (error) {
    if (!(error instanceof MarkersNotFound)) throw error;

    control.recordFailure(bookId, "NO_MARKERS", now);
    return 3;
  }

  // Storage completes every artifact before the control-file append.
  storage.write(bookId, parts.header, parts.body);
  control.markDownloaded(bookId);

  return 0;
}

export async function splitCachedBook({
  workspace,
  bookId,
  layout = "time",
  now = new Date(),
}) {
  validateInputs(bookId, now);

  const instant = new Date(now.getTime());
  const storage = makeStorage(workspace, layout, instant);

  return withRunLock(workspace, async () => {
    let text;

    try {
      text = readFileSync(
        resolve(workspace, "raw", `${bookId}.txt`),
        "utf8",
      );
    } catch (error) {
      if (error.code === "ENOENT") return 3;
      throw error;
    }

    return ingestText({
      workspace,
      bookId,
      text,
      storage,
      now: instant,
    });
  });
}
export async function downloadBooks({
  workspace,
  bookIds,
  layout = "time",
  now = new Date(),
  workers = 1,
  sourceBase = "https://www.gutenberg.org",
  downloaderFactory = createDownloader,
}) {
  if (!Number.isSafeInteger(workers) || workers <= 0) {
    throw new RangeError("workers must be a positive safe integer");
  }

  const ids = [...new Set(bookIds)];
  for (const bookId of ids) {
    validateInputs(bookId, now);
  }

  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new TypeError("A valid ingestion instant must be supplied");
  }

  const instant = new Date(now.getTime());
  const storage = makeStorage(workspace, layout, instant);

  return withRunLock(workspace, async () => {
    const control = new ControlFiles(workspace);
    const downloaded = control.downloadedIds();
    const pendingIds = ids.filter((id) => !downloaded.has(id));

    if (pendingIds.length === 0) return 0;

    const downloader = downloaderFactory({ sourceBase });
    const pending = new Map();
    let next = 0;
    let exitCode = 0;

    function startNext() {
      if (next >= pendingIds.length) return;

      const bookId = pendingIds[next];
      next += 1;

      // Capture failures immediately, including synchronous adapter failures.
      const task = Promise.resolve()
        .then(() => downloader.download(bookId))
        .then(
          (text) => ({ text }),
          (error) => ({ error }),
        );

      pending.set(bookId, task);
    }

    try {
      for (let i = 0; i < Math.min(workers, pendingIds.length); i += 1) {
        startNext();
      }

      // Commit results in input order, regardless of network completion order.
      for (const bookId of pendingIds) {
        const result = await pending.get(bookId);
        pending.delete(bookId);
        startNext();

        if ("error" in result) {
          if (!(result.error instanceof DownloadError)) {
            throw result.error;
          }

          const reason = result.error.reason;
          control.recordFailure(bookId, reason, instant);

          if (reason === "DOWNLOAD_ERROR") {
            exitCode = 1;
          } else if (exitCode === 0) {
            exitCode = 3;
          }

          continue;
        }

        // Cache every successful HTTP response before attempting the split.
        atomicWrite(
          resolve(workspace, "raw", `${bookId}.txt`),
          result.text,
        );

        const splitCode = ingestText({
          workspace,
          bookId,
          text: result.text,
          storage,
          now: instant,
        });

        if (splitCode === 3 && exitCode === 0) {
          exitCode = 3;
        }
      }

      return exitCode;
    } finally {
      // Keep the workspace locked until outstanding requests have settled.
      try {
        await Promise.all(pending.values());
      } finally {
        await downloader.close();
      }
    }
  });
}