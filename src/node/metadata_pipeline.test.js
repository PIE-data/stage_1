import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { splitCachedBook } from "./ingestion.js";
import { generateMetadata } from "./metadata_pipeline.js";
import { MetadataStore } from "./datamart/metadata_store.js";
import { acquireRunLock } from "./control/run_lock.js";

const STAMP = "2026-09-17T14:03:11Z";

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "metadata-pipeline-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

async function prepare(workspace, layout, bookId = 42) {
  mkdirSync(join(workspace, "raw"), { recursive: true });

  writeFileSync(
    join(workspace, "raw", `${bookId}.txt`),
    [
      "Title: Café",
      "Language: English",
      "*** START OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
      "café",
      "*** END OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
    ].join("\n"),
    "utf8",
  );

  assert.equal(await splitCachedBook({
    workspace,
    layout,
    bookId,
    now: new Date(STAMP),
  }), 0);
}

for (const layout of ["book", "hash", "time"]) {
  test(`metadata preserves ingestion time and is idempotent for ${layout}`, async (t) => {
    const workspace = fixture(t);
    await prepare(workspace, layout);

    const options = { workspace, layout, bookId: 42, batchSize: 1 };

    const first = await generateMetadata(options);
    assert.equal(first.exitCode, 0);
    assert.equal(first.processed, 1);
    assert.equal(first.written, 1);
    assert.equal(first.metaFilesWritten, 0);

    const store = new MetadataStore(workspace);
    let record;

    try {
      record = store.byId(42);
    } finally {
      store.close();
    }

    assert.equal(record.title, "Café");
    assert.equal(record.language, "en");
    assert.equal(record.ingested_at, STAMP);
    assert.equal(record.body_bytes, Buffer.byteLength("café\n"));

    if (layout === "book") {
      assert.deepEqual(
        JSON.parse(readFileSync(
          join(workspace, "datalake", "books", "42", "meta.json"),
          "utf8",
        )),
        record,
      );
    }

    const repeated = await generateMetadata(options);
    assert.equal(repeated.written, 0);
    assert.equal(repeated.metaFilesWritten, 0);
  });
}

test("metadata validates all receipts before creating the database", async (t) => {
  const workspace = fixture(t);
  await prepare(workspace, "hash", 1);
  await prepare(workspace, "hash", 2);

  rmSync(join(workspace, "control", "ingestion", "hash", "2.json"));

  await assert.rejects(
    generateMetadata({
      workspace,
      layout: "hash",
      all: true,
      batchSize: 1,
    }),
    /Cannot read ingestion receipt/,
  );

  assert.equal(existsSync(join(workspace, "datamarts")), false);
});

test("an empty metadata selection succeeds without creating a database", async (t) => {
  const workspace = fixture(t);

  assert.deepEqual(
    await generateMetadata({ workspace, all: true }),
    {
      exitCode: 0,
      processed: 0,
      written: 0,
      metaFilesWritten: 0,
    },
  );

  assert.equal(existsSync(join(workspace, "datamarts")), false);
});

test("a missing selected book returns exit code 3", async (t) => {
  const workspace = fixture(t);

  const result = await generateMetadata({ workspace, bookId: 42 });

  assert.equal(result.exitCode, 3);
  assert.equal(existsSync(join(workspace, "datamarts")), false);
});

test("metadata repairs book meta.json without rewriting an identical SQL record", async (t) => {
  const workspace = fixture(t);
  await prepare(workspace, "book");

  const options = { workspace, layout: "book", bookId: 42 };
  await generateMetadata(options);

  const metaPath = join(
    workspace, "datalake", "books", "42", "meta.json",
  );
  writeFileSync(metaPath, "{broken", "utf8");

  const repaired = await generateMetadata(options);

  assert.equal(repaired.written, 0);
  assert.equal(repaired.metaFilesWritten, 1);
  assert.equal(
    JSON.parse(readFileSync(metaPath, "utf8")).ingested_at,
    STAMP,
  );
});

test("metadata respects the workspace writer lock", async (t) => {
  const workspace = fixture(t);
  const lock = await acquireRunLock(workspace);

  try {
    await assert.rejects(
      generateMetadata({ workspace, all: true }),
      (error) => error.exitCode === 4,
    );

    assert.equal(existsSync(join(workspace, "datamarts")), false);
  } finally {
    await lock.release();
  }
});