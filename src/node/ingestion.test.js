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
import {
  makeStorage,
  splitCachedBook,
  downloadBooks,
} from "./ingestion.js";
import { acquireRunLock } from "./control/run_lock.js";
import { DownloadError } from "./datalake/downloader.js";

const STAMP = "2026-09-17T14:03:11Z";

const RAW = [
  "Title: Café",
  "Language: English",
  "*** START OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
  "café",
  "*** END OF THE PROJECT GUTENBERG EBOOK SAMPLE ***",
  "Discarded footer",
].join("\n");

function fixture(t) {
  const workspace = mkdtempSync(join(tmpdir(), "ingestion-"));

  t.after(() => {
    rmSync(workspace, { recursive: true, force: true });
  });

  return workspace;
}

function cache(workspace, text) {
  mkdirSync(join(workspace, "raw"), { recursive: true });
  writeFileSync(join(workspace, "raw", "42.txt"), text, "utf8");
}

for (const layout of ["book", "hash", "time"]) {
  test(`offline split writes exact bytes for ${layout}`, async (t) => {
    const workspace = fixture(t);
    const now = new Date(STAMP);
    cache(workspace, RAW);

    assert.equal(
      await splitCachedBook({ workspace, bookId: 42, layout, now }),
      0,
    );

    const storage = makeStorage(workspace, layout, now);
    const paths = storage.lookup(42);
    assert.ok(paths);

    const [headerPath, bodyPath] = paths;

    assert.equal(
      readFileSync(join(workspace, headerPath), "utf8"),
      "Title: Café\nLanguage: English\n",
    );

    assert.deepEqual(
      readFileSync(join(workspace, bodyPath)),
      Buffer.from("café\n", "utf8"),
    );

    assert.equal(
      readFileSync(
        join(workspace, "control", "downloaded_books.txt"),
        "utf8",
      ),
      "42\n",
    );

    if (layout === "book") {
      const metadata = JSON.parse(readFileSync(
        join(workspace, "datalake", "books", "42", "meta.json"),
        "utf8",
      ));

      assert.equal(metadata.title, "Café");
      assert.equal(metadata.ingested_at, STAMP);
      assert.equal(metadata.body_path, bodyPath);
    }
  });
}

test("missing raw input returns 3 without marking the book downloaded", async (t) => {
  const workspace = fixture(t);

  assert.equal(
    await splitCachedBook({
      workspace,
      bookId: 42,
      now: new Date(STAMP),
    }),
    3,
  );

  assert.equal(existsSync(join(workspace, "datalake")), false);
  assert.equal(
    existsSync(join(workspace, "control", "downloaded_books.txt")),
    false,
  );
});

test("missing markers record a failure without writing datalake artifacts", async (t) => {
  const workspace = fixture(t);
  cache(workspace, "Text without markers");

  assert.equal(
    await splitCachedBook({
      workspace,
      bookId: 42,
      now: new Date(STAMP),
    }),
    3,
  );

  assert.equal(existsSync(join(workspace, "datalake")), false);

  assert.equal(
    readFileSync(
      join(workspace, "control", "failed_books.txt"),
      "utf8",
    ),
    `42\tNO_MARKERS\t${STAMP}\n`,
  );

  assert.equal(
    existsSync(join(workspace, "control", "downloaded_books.txt")),
    false,
  );
});

test("offline re-split rewrites content without duplicating the control ID", async (t) => {
  const workspace = fixture(t);
  const options = {
    workspace,
    bookId: 42,
    layout: "hash",
    now: new Date(STAMP),
  };

  cache(workspace, RAW);
  assert.equal(await splitCachedBook(options), 0);

  cache(workspace, RAW.replace("\ncafé\n", "\nUpdated body\n"));
  assert.equal(await splitCachedBook(options), 0);

  const storage = makeStorage(workspace, "hash", options.now);
  const [, bodyPath] = storage.lookup(42);

  assert.equal(
    readFileSync(join(workspace, bodyPath), "utf8"),
    "Updated body\n",
  );

  assert.equal(
    readFileSync(
      join(workspace, "control", "downloaded_books.txt"),
      "utf8",
    ),
    "42\n",
  );
});

test("a locked workspace rejects splitting before writing artifacts", async (t) => {
  const workspace = fixture(t);
  cache(workspace, RAW);

  const lock = await acquireRunLock(workspace);

  try {
    await assert.rejects(
      splitCachedBook({
        workspace,
        bookId: 42,
        now: new Date(STAMP),
      }),
      (error) => error.name === "WorkspaceLockedError" &&
        error.exitCode === 4,
    );

    assert.equal(existsSync(join(workspace, "datalake")), false);
  } finally {
    await lock.release();
  }
});

function fakeDownloader(outcomes) {
  const calls = [];
  let closed = false;

  return {
    calls,
    get closed() {
      return closed;
    },
    factory: () => ({
      async download(bookId) {
        calls.push(bookId);
        const outcome = outcomes.get(bookId);

        if (outcome instanceof Error) throw outcome;
        if (typeof outcome !== "string") {
          throw new Error(`Unexpected request for book ${bookId}`);
        }

        return outcome;
      },
      async close() {
        closed = true;
      },
    }),
  };
}

test("download caches raw text, ingests it and skips completed books", async (t) => {
  const workspace = fixture(t);
  const fake = fakeDownloader(new Map([[42, RAW]]));

  const options = {
    workspace,
    bookIds: [42, 42],
    layout: "hash",
    now: new Date(STAMP),
    downloaderFactory: fake.factory,
  };

  assert.equal(await downloadBooks(options), 0);
  assert.deepEqual(fake.calls, [42]);
  assert.equal(fake.closed, true);

  assert.equal(
    readFileSync(join(workspace, "raw", "42.txt"), "utf8"),
    RAW,
  );

  const storage = makeStorage(workspace, "hash", options.now);
  assert.ok(storage.lookup(42));

  assert.equal(await downloadBooks(options), 0);
  assert.deepEqual(fake.calls, [42]);

  assert.equal(
    readFileSync(
      join(workspace, "control", "downloaded_books.txt"),
      "utf8",
    ),
    "42\n",
  );
});

test("download continues after failures and gives DOWNLOAD_ERROR precedence", async (t) => {
  const workspace = fixture(t);
  const fake = fakeDownloader(new Map([
    [1, new DownloadError("Missing", "NOT_FOUND", 404)],
    [2, "No markers here"],
    [3, new DownloadError("Unavailable", "DOWNLOAD_ERROR", 503)],
    [4, RAW],
  ]));

  assert.equal(await downloadBooks({
    workspace,
    bookIds: [1, 2, 3, 4],
    layout: "hash",
    now: new Date(STAMP),
    workers: 2,
    downloaderFactory: fake.factory,
  }), 1);

  assert.equal(
    readFileSync(
      join(workspace, "control", "failed_books.txt"),
      "utf8",
    ),
    `1\tNOT_FOUND\t${STAMP}\n` +
    `2\tNO_MARKERS\t${STAMP}\n` +
    `3\tDOWNLOAD_ERROR\t${STAMP}\n`,
  );

  assert.equal(
    readFileSync(
      join(workspace, "control", "downloaded_books.txt"),
      "utf8",
    ),
    "4\n",
  );

  assert.equal(
    readFileSync(join(workspace, "raw", "2.txt"), "utf8"),
    "No markers here",
  );

  assert.equal(existsSync(join(workspace, "raw", "1.txt")), false);
  assert.equal(existsSync(join(workspace, "raw", "3.txt")), false);
  assert.equal(fake.closed, true);
});

test("not-found and missing-marker failures produce exit code 3", async (t) => {
  const workspace = fixture(t);
  const fake = fakeDownloader(new Map([
    [1, new DownloadError("Missing", "NOT_FOUND", 404)],
    [2, "No markers"],
  ]));

  assert.equal(await downloadBooks({
    workspace,
    bookIds: [1, 2],
    now: new Date(STAMP),
    downloaderFactory: fake.factory,
  }), 3);

  assert.equal(existsSync(join(workspace, "datalake")), false);
});

test("download overlaps requests and commits in input order", async (t) => {
  const workspace = fixture(t);
  const calls = [];
  let releaseFirst;
  let active = 0;
  let peak = 0;

  const firstGate = new Promise((resolveGate) => {
    releaseFirst = resolveGate;
  });

  const factory = () => ({
    async download(bookId) {
      calls.push(bookId);
      active += 1;
      peak = Math.max(peak, active);

      try {
        if (bookId === 1) {
          await firstGate;
        } else if (bookId === 2) {
          releaseFirst();
        }
        return RAW;
      } finally {
        active -= 1;
      }
    },
    async close() {},
  });

  assert.equal(await downloadBooks({
    workspace,
    bookIds: [1, 2, 3, 4],
    layout: "hash",
    now: new Date(STAMP),
    workers: 2,
    downloaderFactory: factory,
  }), 0);

  assert.equal(peak, 2);
  assert.deepEqual(calls, [1, 2, 3, 4]);

  assert.equal(
    readFileSync(
      join(workspace, "control", "downloaded_books.txt"),
      "utf8",
    ),
    "1\n2\n3\n4\n",
  );
});

test("a locked workspace starts no download requests", async (t) => {
  const workspace = fixture(t);
  const fake = fakeDownloader(new Map([[42, RAW]]));
  const lock = await acquireRunLock(workspace);

  try {
    await assert.rejects(
      downloadBooks({
        workspace,
        bookIds: [42],
        now: new Date(STAMP),
        downloaderFactory: fake.factory,
      }),
      (error) => error.exitCode === 4,
    );

    assert.deepEqual(fake.calls, []);
    assert.equal(existsSync(join(workspace, "raw")), false);
  } finally {
    await lock.release();
  }
});

test("ingestion persists layout receipts and re-split updates the instant", async (t) => {
  for (const layout of ["book", "hash", "time"]) {
    const workspace = fixture(t);
    cache(workspace, RAW);

    const firstInstant = new Date(STAMP);

    assert.equal(await splitCachedBook({
      workspace,
      bookId: 42,
      layout,
      now: firstInstant,
    }), 0);

    const receiptPath = join(
      workspace, "control", "ingestion", layout, "42.json",
    );

    const first = JSON.parse(readFileSync(receiptPath, "utf8"));
    const paths = makeStorage(workspace, layout, firstInstant).lookup(42);

    assert.deepEqual(first, {
      book_id: 42,
      header_path: paths[0],
      body_path: paths[1],
      ingested_at: STAMP,
    });

    // Stay in the same hour to avoid creating a second time-layout copy.
    const secondStamp = "2026-09-17T14:30:00Z";

    assert.equal(await splitCachedBook({
      workspace,
      bookId: 42,
      layout,
      now: new Date(secondStamp),
    }), 0);

    const second = JSON.parse(readFileSync(receiptPath, "utf8"));
    assert.equal(second.ingested_at, secondStamp);

    assert.equal(
      readFileSync(
        join(workspace, "control", "downloaded_books.txt"),
        "utf8",
      ),
      "42\n",
    );
  }
});

test("time lookup selects the newest complete bucket after re-splitting", async (t) => {
  const workspace = fixture(t);
  cache(workspace, RAW);

  assert.equal(await splitCachedBook({
    workspace,
    bookId: 42,
    layout: "time",
    now: new Date(STAMP),
  }), 0);

  cache(workspace, RAW.replace("\ncafé\n", "\nUpdated body\n"));

  const laterStamp = "2026-09-18T09:15:00Z";

  assert.equal(await splitCachedBook({
    workspace,
    bookId: 42,
    layout: "time",
    now: new Date(laterStamp),
  }), 0);

  const storage = makeStorage(workspace, "time");
  const paths = storage.lookup(42);

  assert.deepEqual(paths, [
    "datalake/20260918/09/42.header.txt",
    "datalake/20260918/09/42.body.txt",
  ]);

  assert.equal(
    readFileSync(join(workspace, paths[1]), "utf8"),
    "Updated body\n",
  );

  // Previous copies remain available on disk.
  assert.equal(
    readFileSync(
      join(workspace, "datalake", "20260917", "14", "42.body.txt"),
      "utf8",
    ),
    "café\n",
  );

  const { readIngestionReceipt } = await import(
    "./control/ingestion_receipts.js"
  );

  const receipt = readIngestionReceipt({
    workspace,
    layout: "time",
    bookId: 42,
    paths,
  });

  assert.equal(receipt.ingested_at, laterStamp);

  // A newer incomplete bucket must not hide a complete book.
  const incomplete = join(workspace, "datalake", "20260919", "10");
  mkdirSync(incomplete, { recursive: true });
  writeFileSync(join(incomplete, "42.body.txt"), "Incomplete\n", "utf8");

  assert.deepEqual(storage.lookup(42), paths);

  // Removing the selected body must be observed without a cached result.
  rmSync(join(workspace, paths[1]));

  const fallback = storage.lookup(42);

  assert.deepEqual(fallback, [
    "datalake/20260917/14/42.header.txt",
    "datalake/20260917/14/42.body.txt",
  ]);

  // The newer receipt must not be silently applied to the older copy.
  assert.throws(
    () => readIngestionReceipt({
      workspace,
      layout: "time",
      bookId: 42,
      paths: fallback,
    }),
    /Receipt paths/,
  );
});