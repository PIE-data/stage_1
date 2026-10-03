package index

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"engine/core"
)

// ---------------------------------------------------------------------------
// Folder backend tests
// ---------------------------------------------------------------------------

func makeFolderBackend(t *testing.T) *FolderBackend {
	t.Helper()
	ws := t.TempDir()
	b, err := NewFolderBackend(ws)
	if err != nil {
		t.Fatalf("NewFolderBackend: %v", err)
	}
	return b
}

// SPEC §6.2: bucket is uppercase first code point if A–Z, else _.
// All terms are lowercased by the pipeline, so every term should land in _.
func TestFolderBackend_Bucket(t *testing.T) {
	cases := []struct {
		term   string
		bucket string
	}{
		{"hello", "H"}, // ASCII letter → its uppercase
		{"alpha", "A"},
		{"zebra", "Z"},
		{"café", "C"},   // only the FIRST code point matters
		{"éclair", "_"}, // non-ASCII first code point → _
		{"123ab", "_"},  // digit first → _
	}

	for _, tc := range cases {
		got := termBucket(tc.term)
		if got != tc.bucket {
			t.Errorf("termBucket(%q) = %q, want %q", tc.term, got, tc.bucket)
		}
	}
}

// SPEC §6.2: safe_term encodes every code point outside [a-z0-9] as %XX of its UTF-8 bytes.
func TestFolderBackend_SafeTerm(t *testing.T) {
	cases := []struct {
		term string
		want string
	}{
		{"hello", "hello"},
		{"hello world", "hello%20world"},
		{"don't", "don%27t"},
		{"café", "caf%C3%A9"},   // é = U+00E9 → UTF-8 0xC3 0xA9
		{"naïve", "na%C3%AFve"}, // ï = U+00EF → UTF-8 0xC3 0xAF
		{"a&b", "a%26b"},
		{"abc123", "abc123"},
	}

	for _, tc := range cases {
		got := safeTerm(tc.term)
		if got != tc.want {
			t.Errorf("safeTerm(%q) = %q, want %q", tc.term, got, tc.want)
		}
	}
}

// SPEC §6.2: file exists in the correct bucket directory after IndexBook.
func TestFolderBackend_FileLocation(t *testing.T) {
	b := makeFolderBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"hello"}, 0), false)

	// Bucket is the uppercase first letter (SPEC §6.2, as in Python and Node)
	expected := filepath.Join(b.workspace, "datamarts", "inverted_index", "H", "hello.txt")
	if _, err := os.Stat(expected); err != nil {
		t.Errorf("expected posting file at %s, got: %v", expected, err)
	}
}

// SPEC §6.2: file format is <book_id>\t<tf>\t<pos1>,<pos2>,...\n per line, sorted by book_id.
func TestFolderBackend_FileFormat_WithPositions(t *testing.T) {
	b := makeFolderBackend(t)
	defer b.Close()

	_ = b.IndexBook(3, syntheticTokens([]string{"word", "word"}, 0), true)
	_ = b.IndexBook(1, syntheticTokens([]string{"word"}, 5), true)

	f, err := os.Open(filepath.Join(b.workspace, "datamarts", "inverted_index", "W", "word.txt"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	var lines []string
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}

	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d: %v", len(lines), lines)
	}
	// First line: book 1 (sorted ascending), tf=1, one position
	if !strings.HasPrefix(lines[0], "1\t1\t") {
		t.Errorf("line 0: expected '1\\t1\\t...', got %q", lines[0])
	}
	// Second line: book 3, tf=2, two positions
	if !strings.HasPrefix(lines[1], "3\t2\t") {
		t.Errorf("line 1: expected '3\\t2\\t...', got %q", lines[1])
	}
}

func TestFolderBackend_FileFormat_WithoutPositions(t *testing.T) {
	b := makeFolderBackend(t)
	defer b.Close()

	_ = b.IndexBook(2, syntheticTokens([]string{"word", "word"}, 0), false)

	f, err := os.Open(filepath.Join(b.workspace, "datamarts", "inverted_index", "W", "word.txt"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	var lines []string
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}

	if len(lines) != 1 {
		t.Fatalf("expected 1 line, got %d", len(lines))
	}
	// Without positions: only 2 tab-separated fields
	parts := strings.Split(lines[0], "\t")
	if len(parts) != 2 {
		t.Errorf("expected 2 tab fields (no positions), got %d: %q", len(parts), lines[0])
	}
}

func TestFolderBackend_QueryAND(t *testing.T) {
	b := makeFolderBackend(t)
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

func TestFolderBackend_QueryOR(t *testing.T) {
	b := makeFolderBackend(t)
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

func TestFolderBackend_EmptyTermList(t *testing.T) {
	b := makeFolderBackend(t)
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

// SPEC §6.2 / I4: indexing same book twice must be idempotent.
func TestFolderBackend_Idempotent(t *testing.T) {
	b := makeFolderBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"hello"}, 0)
	_ = b.IndexBook(1, tokens, false)
	_ = b.IndexBook(1, tokens, false) // second time

	entries, _ := b.ExportCanonical()
	if len(entries["hello"].Postings) != 1 {
		t.Error("Idempotency: expected exactly 1 posting for book 1 after double-index")
	}
}

// SPEC §6.2: safe_term encoding handles non-ASCII multi-byte UTF-8 correctly.
func TestFolderBackend_NonASCIITerm(t *testing.T) {
	b := makeFolderBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, []core.Token{{Value: "café", Position: 0}}, false)

	// File must exist at _/caf%C3%A9.txt
	expected := filepath.Join(b.workspace, "datamarts", "inverted_index", "C", "caf%C3%A9.txt")
	if _, err := os.Stat(expected); err != nil {
		t.Errorf("expected file at %s: %v", expected, err)
	}
}
