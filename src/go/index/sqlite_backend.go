package index

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"engine/core"

	_ "modernc.org/sqlite"
)

// SQLiteBackend implements Backend using an embedded SQLite B-tree.
// SPEC §6.3: datamarts/index.db
type SQLiteBackend struct {
	workspace string
	db        *sql.DB
}

// schema is the DDL for the index database per SPEC §6.3.
const sqliteSchema = `
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA cache_size   = -262144;

CREATE TABLE IF NOT EXISTS terms (
    term  TEXT    PRIMARY KEY,
    df    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
    term      TEXT    NOT NULL,
    book_id   INTEGER NOT NULL,
    tf        INTEGER NOT NULL,
    positions TEXT,
    PRIMARY KEY (term, book_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_postings_book ON postings(book_id);
`

// NewSQLiteBackend opens (or creates) the SQLite index database.
func NewSQLiteBackend(workspace string) (*SQLiteBackend, error) {
	dbPath := filepath.Join(workspace, "datamarts", "index.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0755); err != nil {
		return nil, err
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, err
	}

	// One connection: the PRAGMAs (page cache above all) and the TEMP table
	// are per connection, and database/sql would otherwise open more.
	db.SetMaxOpenConns(1)

	if _, err := db.Exec(sqliteSchema); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}

	return &SQLiteBackend{workspace: workspace, db: db}, nil
}

// Close releases the database connection.
func (b *SQLiteBackend) Close() error {
	return b.db.Close()
}

// ---------- Backend implementation -----------------------------------------

// IndexBook merges one book's tokens into the SQLite index.
func (b *SQLiteBackend) IndexBook(bookID int, tokens []core.Token, withPositions bool) error {
	return b.IndexBatch([]BookTokens{{BookID: bookID, Tokens: tokens}}, withPositions)
}

// IndexBatch merges several books into the SQLite index. SPEC §6.3:
//   - INSERT OR REPLACE inside ONE transaction per batch;
//   - each book's rows inserted in primary-key order (term ascending);
//   - terms.df refreshed once, just before the commit, for the terms touched,
//     kept in a TEMP table (never in index.db).
func (b *SQLiteBackend) IndexBatch(books []BookTokens, withPositions bool) error {
	if len(books) == 0 {
		return nil
	}
	tx, err := b.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck

	if _, err := tx.Exec(`CREATE TEMP TABLE IF NOT EXISTS touched_terms (term TEXT PRIMARY KEY)`); err != nil {
		return fmt.Errorf("create temp table: %w", err)
	}
	if _, err := tx.Exec(`DELETE FROM touched_terms`); err != nil {
		return fmt.Errorf("clear temp table: %w", err)
	}
	insert, err := tx.Prepare(`INSERT OR REPLACE INTO postings (term, book_id, tf, positions) VALUES (?, ?, ?, ?)`)
	if err != nil {
		return err
	}
	defer insert.Close()
	touch, err := tx.Prepare(`INSERT OR IGNORE INTO touched_terms (term) VALUES (?)`)
	if err != nil {
		return err
	}
	defer touch.Close()

	for _, book := range books {
		stats := bookTermStats(book.Tokens, withPositions)
		terms := make([]string, 0, len(stats))
		for t := range stats {
			terms = append(terms, t)
		}
		sort.Strings(terms)
		for _, term := range terms {
			ts := stats[term]
			var positions *string
			if withPositions {
				s := intSliceToString(ts.positions)
				positions = &s
			}
			if _, err := insert.Exec(term, book.BookID, ts.tf, positions); err != nil {
				return fmt.Errorf("insert posting (%s, %d): %w", term, book.BookID, err)
			}
			if _, err := touch.Exec(term); err != nil {
				return fmt.Errorf("track touched term: %w", err)
			}
		}
	}

	// IN (subquery), not a JOIN: with the JOIN SQLite may scan the whole
	// postings table instead of seeking each touched term.
	if _, err := tx.Exec(`
		INSERT OR REPLACE INTO terms (term, df)
		SELECT term, COUNT(*) FROM postings
		WHERE term IN (SELECT term FROM touched_terms)
		GROUP BY term
	`); err != nil {
		return fmt.Errorf("refresh df: %w", err)
	}
	return tx.Commit()
}

// Query returns matching book IDs per SPEC §1.1 and §6.3.
func (b *SQLiteBackend) Query(terms []string, mode string, limit int) ([]int, error) {
	if len(terms) == 0 {
		return nil, nil
	}

	var rows *sql.Rows
	var err error

	switch mode {
	case "and":
		// SPEC §6.3: AND-k query.
		// SELECT book_id FROM postings WHERE term IN (…) GROUP BY book_id HAVING COUNT(*) = k
		placeholders := make([]string, len(terms))
		args := make([]any, len(terms))
		for i, t := range terms {
			placeholders[i] = "?"
			args[i] = t
		}
		k := len(terms)
		query := fmt.Sprintf(
			`SELECT book_id FROM postings WHERE term IN (%s) GROUP BY book_id HAVING COUNT(*) = %d ORDER BY book_id`,
			joinStrings(placeholders, ","), k,
		)
		rows, err = b.db.Query(query, args...)

	case "or":
		// SPEC §6.3: single-term is SELECT book_id, tf; for OR we do a UNION approach.
		placeholders := make([]string, len(terms))
		args := make([]any, len(terms))
		for i, t := range terms {
			placeholders[i] = "?"
			args[i] = t
		}
		query := fmt.Sprintf(
			`SELECT DISTINCT book_id FROM postings WHERE term IN (%s) ORDER BY book_id`,
			joinStrings(placeholders, ","),
		)
		rows, err = b.db.Query(query, args...)

	default:
		return nil, fmt.Errorf("unknown mode: %s", mode)
	}

	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var ids []int
	for rows.Next() {
		var id int
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	if limit == 0 {
		return nil, nil
	}
	if limit > 0 && len(ids) > limit {
		ids = ids[:limit]
	}
	return ids, nil
}

// ExportCanonical returns the full index as an in-memory map (SPEC §7).
func (b *SQLiteBackend) ExportCanonical() (map[string]IndexEntry, error) {
	rows, err := b.db.Query(
		`SELECT p.term, p.book_id, p.tf, p.positions, t.df
		 FROM postings p
		 JOIN terms t ON t.term = p.term
		 ORDER BY p.term, p.book_id`,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make(map[string]IndexEntry)
	for rows.Next() {
		var term string
		var bookID, tf int
		var posStr sql.NullString
		var df int
		if err := rows.Scan(&term, &bookID, &tf, &posStr, &df); err != nil {
			return nil, err
		}

		p := Posting{BookID: bookID, TF: tf}
		if posStr.Valid && posStr.String != "" {
			for _, ps := range strings.Split(posStr.String, ",") {
				pos, err := strconv.Atoi(ps)
				if err == nil {
					p.Positions = append(p.Positions, pos)
				}
			}
		}

		entry := result[term]
		entry.DF = df
		entry.Postings = append(entry.Postings, p)
		result[term] = entry
	}
	return result, rows.Err()
}

// HasPositions reports whether the index was built with positions.
func (b *SQLiteBackend) HasPositions() (bool, error) {
	row := b.db.QueryRow(`SELECT positions FROM postings LIMIT 1`)
	var pos sql.NullString
	if err := row.Scan(&pos); err == sql.ErrNoRows {
		return false, nil
	} else if err != nil {
		return false, err
	}
	return pos.Valid, nil
}

// Ensure the interface is satisfied at compile time.
var _ Backend = (*SQLiteBackend)(nil)
