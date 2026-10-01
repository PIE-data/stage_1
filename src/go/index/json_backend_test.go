package index

import (
	"os"
	"path/filepath"
	"testing"

	"engine/core"
)

// ---------------------------------------------------------------------------
// Helpers shared across backend tests
// ---------------------------------------------------------------------------

// syntheticTokens builds a minimal token list for use in tests.
// positions are assigned sequentially starting from posOffset.
func syntheticTokens(words []string, posOffset int) []core.Token {
	out := make([]core.Token, len(words))
	for i, w := range words {
		out[i] = core.Token{Value: w, Position: posOffset + i}
	}
	return out
}

// makeJSONBackend creates a JSONBackend rooted in a temp workspace.
func makeJSONBackend(t *testing.T) *JSONBackend {
	t.Helper()
	ws := t.TempDir()
	b, err := NewJSONBackend(ws)
	if err != nil {
		t.Fatalf("NewJSONBackend: %v", err)
	}
	return b
}

// ---------------------------------------------------------------------------
// JSON backend tests
// ---------------------------------------------------------------------------

func TestJSONBackend_RoundTrip(t *testing.T) {
	b := makeJSONBackend(t)
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
		t.Fatal("expected term 'hello' in index")
	} else if len(e.Postings) != 1 || e.Postings[0].TF != 2 {
		t.Errorf("hello: expected TF=2, got %+v", e.Postings)
	}
	if e, ok := entries["world"]; !ok {
		t.Fatal("expected term 'world' in index")
	} else if len(e.Postings) != 1 || e.Postings[0].TF != 1 {
		t.Errorf("world: expected TF=1, got %+v", e.Postings)
	}
}

func TestJSONBackend_WithPositions(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"cat", "dog", "cat"}, 0)
	if err := b.IndexBook(5, tokens, true); err != nil {
		t.Fatalf("IndexBook: %v", err)
	}

	entries, _ := b.ExportCanonical()
	cat := entries["cat"]
	if len(cat.Postings) != 1 {
		t.Fatalf("expected 1 posting for 'cat', got %d", len(cat.Postings))
	}
	// positions 0 and 2
	if len(cat.Postings[0].Positions) != 2 {
		t.Errorf("cat: expected 2 positions, got %v", cat.Postings[0].Positions)
	}
}

func TestJSONBackend_QueryAND(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"alpha", "beta"}, 0), false)
	_ = b.IndexBook(2, syntheticTokens([]string{"alpha"}, 0), false)
	_ = b.IndexBook(3, syntheticTokens([]string{"beta"}, 0), false)

	// AND: only book 1 has both; limit=-1 means unlimited
	ids, err := b.Query([]string{"alpha", "beta"}, "and", -1)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if len(ids) != 1 || ids[0] != 1 {
		t.Errorf("AND query: expected [1], got %v", ids)
	}
}

func TestJSONBackend_QueryOR(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"alpha"}, 0), false)
	_ = b.IndexBook(2, syntheticTokens([]string{"beta"}, 0), false)

	ids, err := b.Query([]string{"alpha", "beta"}, "or", -1)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if len(ids) != 2 || ids[0] != 1 || ids[1] != 2 {
		t.Errorf("OR query: expected [1,2], got %v", ids)
	}
}

// SPEC §1.1: empty term list MUST match nothing in both modes.
func TestJSONBackend_EmptyTermList(t *testing.T) {
	b := makeJSONBackend(t)
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

// SPEC §7: encoding/json must NOT escape <, >, &.
// We verify by checking the raw file bytes for a term containing "&".
func TestJSONBackend_NoHTMLEscape(t *testing.T) {
	ws := t.TempDir()
	b, _ := NewJSONBackend(ws)
	defer b.Close()

	// Index a term that contains & — after the tokenizer this would never
	// appear, but the export-canonical path must still not escape it.
	// We inject directly via a synthetic token whose value contains "&"
	// to test the serialiser in isolation.
	_ = b.IndexBook(1, []core.Token{{Value: "a&b", Position: 0}}, false)

	data, err := os.ReadFile(filepath.Join(ws, "datamarts", "inverted_index.json"))
	if err != nil {
		t.Fatalf("read index file: %v", err)
	}
	if contains(data, []byte(`\u0026`)) {
		t.Error("JSON backend escaped & as \\u0026 — SetEscapeHTML must be false")
	}
	if !contains(data, []byte(`a&b`)) {
		t.Errorf("expected literal 'a&b' in index, file content: %s", data)
	}
}

func contains(haystack, needle []byte) bool {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		match := true
		for j := range needle {
			if haystack[i+j] != needle[j] {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

// SPEC §6.1: indexing the same book twice must produce idempotent results (I4).
func TestJSONBackend_Idempotent(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	tokens := syntheticTokens([]string{"hello", "world"}, 0)
	_ = b.IndexBook(1, tokens, false)
	_ = b.IndexBook(1, tokens, false) // second time

	entries, _ := b.ExportCanonical()
	if len(entries["hello"].Postings) != 1 {
		t.Error("Idempotency: expected exactly 1 posting for book 1 after double-index")
	}
}

// SPEC §1.1: limit=0 returns nothing.
func TestJSONBackend_LimitZero(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	_ = b.IndexBook(1, syntheticTokens([]string{"hello"}, 0), false)
	ids, err := b.Query([]string{"hello"}, "or", 0)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	// limit=0 → nothing
	if len(ids) != 0 {
		t.Errorf("limit=0: expected [], got %v", ids)
	}
}

// SPEC §1.1: positive limit truncates after ordering.
func TestJSONBackend_LimitTruncates(t *testing.T) {
	b := makeJSONBackend(t)
	defer b.Close()

	for _, id := range []int{3, 1, 2} {
		_ = b.IndexBook(id, syntheticTokens([]string{"hello"}, 0), false)
	}
	ids, _ := b.Query([]string{"hello"}, "or", 2)
	if len(ids) != 2 || ids[0] != 1 || ids[1] != 2 {
		t.Errorf("limit=2: expected [1,2], got %v", ids)
	}
}
