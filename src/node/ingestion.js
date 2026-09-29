import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { splitText, MarkersNotFound } from "./datalake/splitter.js";
import { BookStorage } from "./datalake/book_storage.js";
import { HashStorage } from "./datalake/hash_storage.js";
import { TimeStorage } from "./datalake/time_storage.js";
import { ControlFiles } from "./control/files.js";
import { withRunLock } from "./control/run_lock.js";

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