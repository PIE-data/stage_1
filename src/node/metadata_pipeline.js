import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";

import { makeStorage } from "./ingestion.js";
import { atomicWrite } from "./datalake/atomic.js";
import { withRunLock } from "./control/run_lock.js";
import { readIngestionReceipt } from "./control/ingestion_receipts.js";
import { buildMetadataRecord } from "./datamart/metadata_record.js";
import { MetadataStore } from "./datamart/metadata_store.js";

const FIRST_INSTANT = new Date("0000-01-01T00:00:00Z");

function updateBookMetadata(workspace, record) {
  const path = resolve(
    workspace,
    dirname(record.body_path),
    "meta.json",
  );

  let previous;

  try {
    previous = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT" && !(error instanceof SyntaxError)) {
      throw error;
    }
  }

  if (isDeepStrictEqual(previous, record)) return false;

  atomicWrite(path, `${JSON.stringify(record)}\n`);
  return true;
}

export async function generateMetadata({
  workspace,
  layout = "time",
  bookId,
  all = false,
  batchSize = 500,
}) {
  if (typeof all !== "boolean" || (bookId !== undefined) === all) {
    throw new TypeError("Choose exactly one of bookId or all");
  }

  if (
    bookId !== undefined &&
    (!Number.isSafeInteger(bookId) || bookId <= 0)
  ) {
    throw new RangeError("bookId must be a positive safe integer");
  }

  if (!Number.isSafeInteger(batchSize) || batchSize <= 0) {
    throw new RangeError("batchSize must be a positive safe integer");
  }

  return withRunLock(workspace, async () => {
    const storage = makeStorage(workspace, layout);

    const ids = all
      ? [...new Set(storage.listNew(FIRST_INSTANT))]
        .sort((a, b) => a - b)
      : [bookId];

    const selected = [];

    // Validate every selected receipt before opening or writing SQLite.
    for (const id of ids) {
      const paths = storage.lookup(id);

      if (!paths) {
        return {
          exitCode: 3,
          processed: 0,
          written: 0,
          metaFilesWritten: 0,
        };
      }

      const receipt = readIngestionReceipt({
        workspace,
        layout,
        bookId: id,
        paths,
      });

      selected.push({ id, paths, receipt });
    }

    if (selected.length === 0) {
      return {
        exitCode: 0,
        processed: 0,
        written: 0,
        metaFilesWritten: 0,
      };
    }

    const store = new MetadataStore(workspace);
    let written = 0;
    let metaFilesWritten = 0;

    try {
      for (let offset = 0; offset < selected.length; offset += batchSize) {
        const batch = selected.slice(offset, offset + batchSize);

        const records = batch.map(({ id, paths, receipt }) =>
          buildMetadataRecord({
            bookId: id,
            workspace,
            headerPath: paths[0],
            bodyPath: paths[1],
            ingestedAt: new Date(receipt.ingested_at),
          }),
        );

        written += store.writeMany(records, { batchSize });

        if (layout === "book") {
          for (const record of records) {
            if (updateBookMetadata(workspace, record)) {
              metaFilesWritten += 1;
            }
          }
        }
      }
    } finally {
      store.close();
    }

    return {
      exitCode: 0,
      processed: selected.length,
      written,
      metaFilesWritten,
    };
  });
}