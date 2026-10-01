package datamart

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

type Store struct {
	db *sql.DB
}

func NewStore(workspace string) (*Store, error) {
	dbPath := filepath.Join(workspace, "datamarts", "metadata.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0755); err != nil {
		return nil, err
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, err
	}

	_, _ = db.Exec("PRAGMA journal_mode = WAL;")
	_, _ = db.Exec("PRAGMA synchronous = NORMAL;")

	schema := `
	CREATE TABLE IF NOT EXISTS books (
		book_id      INTEGER PRIMARY KEY,
		title        TEXT    NOT NULL,
		author       TEXT,
		language     TEXT,
		release_date TEXT,
		header_path  TEXT    NOT NULL,
		body_path    TEXT    NOT NULL,
		body_bytes   INTEGER NOT NULL,
		sha256       TEXT    NOT NULL,
		ingested_at  TEXT    NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_books_author   ON books(author);
	CREATE INDEX IF NOT EXISTS idx_books_language ON books(language);
	CREATE INDEX IF NOT EXISTS idx_books_title    ON books(title);
	`
	_, err = db.Exec(schema)
	if err != nil {
		return nil, err
	}

	return &Store{db: db}, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) GetByID(bookID int) (*MetadataRecord, error) {
	query := `SELECT book_id, title, author, language, release_date, header_path, body_path, body_bytes, sha256, ingested_at 
	          FROM books WHERE book_id = ?`
	row := s.db.QueryRow(query, bookID)

	var r MetadataRecord
	err := row.Scan(&r.BookID, &r.Title, &r.Author, &r.Language, &r.ReleaseDate, &r.HeaderPath, &r.BodyPath, &r.BodyBytes, &r.SHA256, &r.IngestedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

func recordsEqual(a, b *MetadataRecord) bool {
	if a.BookID != b.BookID || a.Title != b.Title || a.HeaderPath != b.HeaderPath || a.BodyPath != b.BodyPath || a.BodyBytes != b.BodyBytes || a.SHA256 != b.SHA256 || a.IngestedAt != b.IngestedAt {
		return false
	}
	deref := func(str *string) string {
		if str == nil { return "" }
		return *str
	}
	return deref(a.Author) == deref(b.Author) && deref(a.Language) == deref(b.Language) && deref(a.ReleaseDate) == deref(b.ReleaseDate)
}

func (s *Store) Upsert(records []MetadataRecord, batchSize int) (int, error) {
	if batchSize <= 0 {
		return 0, fmt.Errorf("batch size must be positive")
	}

	written := 0
	query := `INSERT OR REPLACE INTO books (
		book_id, title, author, language, release_date, header_path, body_path, body_bytes, sha256, ingested_at
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`

	for start := 0; start < len(records); start += batchSize {
		end := start + batchSize
		if end > len(records) {
			end = len(records)
		}
		batch := records[start:end]

		tx, err := s.db.Begin()
		if err != nil {
			return written, err
		}

		stmt, err := tx.Prepare(query)
		if err != nil {
			tx.Rollback()
			return written, err
		}

		for _, record := range batch {
			existing, err := s.GetByID(record.BookID)
			
			if err == nil && existing != nil && recordsEqual(existing, &record) {
				continue 
			}

			_, err = stmt.Exec(record.BookID, record.Title, record.Author, record.Language, record.ReleaseDate, record.HeaderPath, record.BodyPath, record.BodyBytes, record.SHA256, record.IngestedAt)
			if err != nil {
				stmt.Close()
				tx.Rollback()
				return written, err
			}
			written++
		}
		stmt.Close()
		err = tx.Commit()
		if err != nil {
			return written, err
		}
	}

	return written, nil
}
