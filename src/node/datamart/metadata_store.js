import { mkdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

const FIELDS = [
  "book_id", "title", "author", "language", "release_date",
  "header_path", "body_path", "body_bytes", "sha256", "ingested_at",
];

export class MetadataStore {
  constructor(workspace) {
    const directory = resolve(workspace, "datamarts");
    mkdirSync(directory, { recursive: true });

    this.db = new DatabaseSync(join(directory, "metadata.db"));

    try {
      this.db.exec(`
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA cache_size = -262144;

        CREATE TABLE IF NOT EXISTS books (
          book_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          author TEXT,
          language TEXT,
          release_date TEXT,
          header_path TEXT NOT NULL,
          body_path TEXT NOT NULL,
          body_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          ingested_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
        CREATE INDEX IF NOT EXISTS idx_books_language ON books(language);
        CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
      `);

      this.select = this.db.prepare(
        "SELECT * FROM books WHERE book_id = ?",
      );

      this.insert = this.db.prepare(`
        INSERT OR REPLACE INTO books (${FIELDS.join(", ")})
        VALUES (${FIELDS.map(() => "?").join(", ")})
      `);
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  writeBatch(records) {
    let written = 0;
    this.db.exec("BEGIN IMMEDIATE");

    try {
      for (const record of records) {
        const previous = this.select.get(record.book_id);

        if (
          previous &&
          FIELDS.every((field) => previous[field] === record[field])
        ) {
          continue;
        }

        this.insert.run(...FIELDS.map((field) => record[field]));
        written += 1;
      }

      this.db.exec("COMMIT");
      return written;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  writeMany(records, { batchSize = 500 } = {}) {
    if (!Number.isSafeInteger(batchSize) || batchSize < 1) {
      throw new RangeError("batchSize must be a positive safe integer");
    }

    let batch = [];
    let written = 0;

    for (const record of records) {
      batch.push(record);

      if (batch.length === batchSize) {
        written += this.writeBatch(batch);
        batch = [];
      }
    }

    if (batch.length > 0) {
      written += this.writeBatch(batch);
    }

    return written;
  }

  byId(bookId) {
    const row = this.select.get(bookId);
    return row ? { ...row } : null;
  }

  byAuthor(author) {
    return this.db
      .prepare("SELECT * FROM books WHERE author = ?")
      .all(author)
      .map((row) => ({ ...row }));
  }

  bodyPath(bookId) {
    return this.db
      .prepare("SELECT body_path FROM books WHERE book_id = ?")
      .get(bookId)?.body_path ?? null;
  }

  byTitlePrefix(prefix) {
    return this.db
      .prepare("SELECT * FROM books WHERE title LIKE ? || '%'")
      .all(prefix)
      .map((row) => ({ ...row }));
  }

  countByLanguage() {
    return this.db
      .prepare(
        "SELECT language, COUNT(*) AS count FROM books GROUP BY language",
      )
      .all()
      .map((row) => ({ ...row }));
  }

  close() {
    this.db.close();
  }
}