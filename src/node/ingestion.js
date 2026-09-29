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
import { writeIngestionReceipt } from "./control/ingestion_receipts.js";

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
  layout,
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
  const paths = storage.write(bookId, parts.header, parts.body);

  writeIngestionReceipt({
   workspace,
   layout,
   bookId,
   paths,
   ingestedAt: now,
  });

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
      layout,
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
    const completed = new Map();

    let nextRequest = 0;
    let nextCommit = 0;
    let exitCode = 0;
    let fatal = null;

    function rawPath(bookId) {
      return resolve(workspace, "raw", `${bookId}.txt`);
    }

    // This function is synchronous: workers cannot interleave commits.
    function commitReady() {
      while (
        nextCommit < pendingIds.length &&
        completed.has(nextCommit)
      ) {
        const bookId = pendingIds[nextCommit];
        const outcome = completed.get(nextCommit);

        if (outcome.error) {
          const reason = outcome.error.reason;
          control.recordFailure(bookId, reason, instant);

          if (reason === "DOWNLOAD_ERROR") {
            exitCode = 1;
          } else if (exitCode === 0) {
            exitCode = 3;
          }
        } else {
          // Completed texts stay on disk while earlier requests are pending.
          const text = readFileSync(rawPath(bookId), "utf8");

          const splitCode = ingestText({
            workspace,
            bookId,
            text,
            storage,
            layout,
            now: instant,
          });

          if (splitCode === 3 && exitCode === 0) {
            exitCode = 3;
          }
        }

        completed.delete(nextCommit);
        nextCommit += 1;
      }
    }

    async function worker() {
      try {
        while (fatal === null && nextRequest < pendingIds.length) {
          const index = nextRequest;
          nextRequest += 1;

          const bookId = pendingIds[index];
          let text;
          let outcome;

          try {
            text = await downloader.download(bookId);
            outcome = {};
          } catch (error) {
            if (!(error instanceof DownloadError)) throw error;
            outcome = { error };
          }

          // Another worker may have encountered a fatal filesystem error.
          if (fatal !== null) return;

          if (!outcome.error) {
            // Cache before splitting and release the response text promptly.
            atomicWrite(rawPath(bookId), text);
            text = undefined;
          }

          completed.set(index, outcome);
          commitReady();

          // Claim another request without waiting for earlier books.
        }
      } catch (error) {
        // Stop scheduling, but let all outstanding requests settle.
        if (fatal === null) fatal = { error };
      }
    }

    try {
      const pool = Array.from(
        { length: Math.min(workers, pendingIds.length) },
        () => worker(),
      );

      // There is one promise per worker, not one per book.
      await Promise.all(pool);

      if (fatal !== null) throw fatal.error;

      return exitCode;
    } finally {
      // All workers have settled before the downloader and lock are released.
      await downloader.close();
    }
  });
}