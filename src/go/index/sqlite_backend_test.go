package index

import (
	"database/sql"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"
)

// ---------------------------------------------------------------------------
// SQLite backend tests
// ---------------------------------------------------------------------------

func makeSQLiteBackend(t *testing.T) *SQLiteBackend {
	t.Helper()
	ws := t.TempDir()
	b, err := NewSQLiteBackend(ws)
	if err != nil {
		t.Fatalf("NewSQLiteBackend: %v", err)
	}
	return b
}

// SPEC §6.3: postings table MUST be WITHOUT ROWID.
func TestSQLiteBackend_WithoutROWID(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	dbPath := filepath.Join(b.workspace, "datamarts", "index.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	row := db.QueryRow(`SELECT sql FROM sqlite_master WHERE type='table' AND name='postings'`)
	var ddl string
	if err := row.Scan(&ddl); err != nil {
		t.Fatalf("query sqlite_master: %v", err)
	}
	// The DDL must contain WITHOUT ROWID
	if !containsCI(ddl, "WITHOUT ROWID") {
		t.Errorf("postings table DDL does not contain WITHOUT ROWID:\n%s", ddl)
	}
}

// SPEC §6.3: terms table must exist with correct schema.
func TestSQLiteBackend_TermsTable(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	dbPath := filepath.Join(b.workspace, "datamarts", "index.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	row := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='terms'`)
	var count int
	if err := row.Scan(&count); err != nil || count != 1 {
		t.Errorf("terms table missing or error: count=%d, err=%v", count, err)
	}
}

func TestSQLiteBackend_RoundTrip(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"hello", "world", "hello"}, 0)
	if err := b.IndexBook(1, tokens, false); err != nil {
		t.Fatalf("IndexBook: %v", err)
	}

	entries, err := b.ExportCanonical()
	if err != nil {
		t.Fatalf("ExportCanonical: %v", err)
	}

	if e, ok := entries["hello"]; !ok {
		t.Fatal("'hello' not in index")
	} else if len(e.Postings) != 1 || e.Postings[0].TF != 2 {
		t.Errorf("hello: expected TF=2, got %+v", e.Postings)
	}
}

func TestSQLiteBackend_WithPositions(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"cat", "dog", "cat"}, 0)
	if err := b.IndexBook(5, tokens, true); err != nil {
		t.Fatalf("IndexBook: %v", err)
	}

	entries, _ := b.ExportCanonical()
	cat := entries["cat"]
	if len(cat.Postings) != 1 {
		t.Fatalf("expected 1 posting for cat, got %d", len(cat.Postings))
	}
	if len(cat.Postings[0].Positions) != 2 {
		t.Errorf("cat: expected 2 positions, got %v", cat.Postings[0].Positions)
	}
}

// SPEC §6.3: terms.df must equal the number of distinct books containing the term.
func TestSQLiteBackend_DFCorrect(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"alpha", "beta"}, 0), false)
	_ = b.IndexBook(2, syntheticTokens([]string{"alpha"}, 0), false)
	_ = b.IndexBook(3, syntheticTokens([]string{"beta", "gamma"}, 0), false)

	entries, _ := b.ExportCanonical()
	if entries["alpha"].DF != 2 {
		t.Errorf("alpha df: expected 2, got %d", entries["alpha"].DF)
	}
	if entries["beta"].DF != 2 {
		t.Errorf("beta df: expected 2, got %d", entries["beta"].DF)
	}
	if entries["gamma"].DF != 1 {
		t.Errorf("gamma df: expected 1, got %d", entries["gamma"].DF)
	}
}

func TestSQLiteBackend_QueryAND(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"alpha", "beta"}, 0), false)
	_ = b.IndexBook(2, syntheticTokens([]string{"alpha"}, 0), false)
	_ = b.IndexBook(3, syntheticTokens([]string{"beta"}, 0), false)

	ids, err := b.Query([]string{"alpha", "beta"}, "and", -1)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if len(ids) != 1 || ids[0] != 1 {
		t.Errorf("AND: expected [1], got %v", ids)
	}
}

func TestSQLiteBackend_QueryOR(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"alpha"}, 0), false)
	_ = b.IndexBook(2, syntheticTokens([]string{"beta"}, 0), false)

	ids, err := b.Query([]string{"alpha", "beta"}, "or", -1)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if len(ids) != 2 || ids[0] != 1 || ids[1] != 2 {
		t.Errorf("OR: expected [1,2], got %v", ids)
	}
}

func TestSQLiteBackend_EmptyTermList(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"hello"}, 0), false)

	for _, mode := range []string{"and", "or"} {
		ids, err := b.Query([]string{}, mode, 0)
		if err != nil {
			t.Fatalf("Query(%s): %v", mode, err)
		}
		if len(ids) != 0 {
			t.Errorf("empty terms mode=%s: expected [], got %v", mode, ids)
		}
	}
}

// SPEC I4: indexing the same book twice must be idempotent.
func TestSQLiteBackend_Idempotent(t *testing.T) {
	b := makeSQLiteBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"hello", "world"}, 0)
	_ = b.IndexBook(1, tokens, false)
	_ = b.IndexBook(1, tokens, false) // second time

	entries, _ := b.ExportCanonical()
	if len(entries["hello"].Postings) != 1 {
		t.Error("Idempotency: expected exactly 1 posting for book 1 after double-index")
	}
	if entries["hello"].Postings[0].TF != 1 {
		t.Errorf("Idempotency: TF should be 1, got %d", entries["hello"].Postings[0].TF)
	}
}

// Helper: case-insensitive substring check.
func containsCI(s, sub string) bool {
	s2, sub2 := make([]byte, len(s)), make([]byte, len(sub))
	for i := range s {
		c := s[i]
		if 'a' <= c && c <= 'z' {
			c -= 32
		}
		s2[i] = c
	}
	for i := range sub {
		c := sub[i]
		if 'a' <= c && c <= 'z' {
			c -= 32
		}
		sub2[i] = c
	}
	return contains(s2, sub2)
}
