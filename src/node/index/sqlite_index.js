import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { buildPostings, compareTerms, writeCanonical } from "./json_index.js";

export class SqliteIndex {
  constructor(workspace, { positions = false } = {}) {
    if (typeof positions !== "boolean") {
      throw new TypeError("positions must be a boolean");
    }

    this.positions = positions;
    this.path = join(resolve(workspace), "datamarts", "index.db");
    mkdirSync(dirname(this.path), { recursive: true });

    this.db = new DatabaseSync(this.path);

    try {
      this.db.exec(`
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA cache_size = -262144;

        CREATE TABLE IF NOT EXISTS terms (
          term TEXT PRIMARY KEY,
          df INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS postings (
          term TEXT NOT NULL,
          book_id INTEGER NOT NULL,
          tf INTEGER NOT NULL,
          positions TEXT,
          PRIMARY KEY (term, book_id)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_postings_book
          ON postings(book_id);

        CREATE TEMP TABLE touched_terms (
          term TEXT PRIMARY KEY
        ) WITHOUT ROWID;
      `);

      const incompatible = this.db.prepare(`
        SELECT 1 FROM postings
        WHERE ${positions ? "positions IS NULL" : "positions IS NOT NULL"}
        LIMIT 1
      `).get();

      if (incompatible) {
        throw new Error("Incompatible positions setting");
      }

      this.insertPosting = this.db.prepare(`
        INSERT OR REPLACE INTO postings (term, book_id, tf, positions)
        VALUES (?, ?, ?, ?)
      `);

      this.touchTerm = this.db.prepare(`
        INSERT OR IGNORE INTO touched_terms (term) VALUES (?)
      `);

      this.touchPrevious = this.db.prepare(`
        INSERT OR IGNORE INTO touched_terms (term)
        SELECT term FROM postings WHERE book_id = ?
      `);

      this.deleteBook = this.db.prepare(`
        DELETE FROM postings WHERE book_id = ?
      `);

      this.selectPostings = this.db.prepare(`
        SELECT book_id, tf, positions
        FROM postings WHERE term = ? ORDER BY book_id
      `);
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  writeBook(bookId, tokens) {
    this.writeBatch([{ bookId, tokens }]);
  }

  writeBatch(books) {
    this.db.exec("BEGIN IMMEDIATE");

    try {
      this.db.exec("DELETE FROM touched_terms");

      for (const { bookId, tokens } of books) {
        const additions = buildPostings(bookId, tokens, this.positions);

        this.touchPrevious.run(bookId);
        this.deleteBook.run(bookId);

        const ordered = [...additions].sort(([left], [right]) =>
          compareTerms(left, right));

        for (const [term, [id, tf, positions]] of ordered) {
          this.insertPosting.run(
            term,
            id,
            tf,
            positions === undefined ? null : positions.join(","),
          );
          this.touchTerm.run(term);
        }
      }

      // Refresh document frequencies once, after all books in this batch.
      // Delete first so terms with no remaining postings also disappear.
      this.db.exec(`
        DELETE FROM terms
        WHERE term IN (SELECT term FROM touched_terms);

        INSERT OR REPLACE INTO terms (term, df)
        SELECT term, COUNT(*)
        FROM postings
        WHERE term IN (SELECT term FROM touched_terms)
        GROUP BY term;

        DELETE FROM touched_terms;
        COMMIT;
      `);
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  writeMany(books, { batchSize = 500 } = {}) {
    if (!Number.isSafeInteger(batchSize) || batchSize <= 0) {
      throw new TypeError("batchSize must be a positive safe integer");
    }

    let batch = [];

    for (const book of books) {
      batch.push(book);

      if (batch.length === batchSize) {
        this.writeBatch(batch);
        batch = [];
      }
    }

    if (batch.length > 0) this.writeBatch(batch);
  }

  postings(term) {
    return this.selectPostings.all(term).map(({ book_id, tf, positions }) =>
      positions === null
        ? [book_id, tf]
        : [book_id, tf, positions.split(",").map(Number)]);
  }

  *allEntries() {
    const terms = this.db.prepare("SELECT term FROM terms").all();

    for (const { term } of terms) {
      yield [term, this.postings(term)];
    }
  }

  exportCanonical(outputPath) {
    // Term by term: 1 000 books do not fit in one string.
    const terms = this.db.prepare("SELECT term FROM terms").all().map(({ term }) => term);
    writeCanonical(outputPath, terms, (term) => this.postings(term));
  }

  close() {
    this.db.close();
  }
}