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
import { makeStorage, splitCachedBook } from "./ingestion.js";
import { acquireRunLock } from "./control/run_lock.js";

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