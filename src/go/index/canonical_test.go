package index

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"engine/core"
)

// ---------------------------------------------------------------------------
// Canonical export conformance test (SPEC §7)
// ---------------------------------------------------------------------------

// expectedCanonicalSHA256 is the frozen hash from spec/golden/expected.sha256.
// It was produced with --positions enabled on the 20 golden books.
const expectedCanonicalSHA256 = "b36095ef3966ccd1b5f273f914859aa83ff82e2499f695a422e364e8ecf0fc94"

// goldenRoot returns the path to spec/golden/ relative to this package.
// This file lives at src/go/index/; spec/golden is three directories up.
func goldenRoot(t *testing.T) string {
	t.Helper()
	return filepath.Join("..", "..", "..", "spec", "golden")
}

// loadGoldenStopwords loads spec/stopwords_en.txt.
func loadGoldenStopwords(t *testing.T) map[string]bool {
	t.Helper()
	path := filepath.Join("..", "..", "..", "spec", "stopwords_en.txt")
	sw, err := core.LoadStopwords(path)
	if err != nil {
		t.Fatalf("load stopwords: %v", err)
	}
	return sw
}

// readGoldenManifest returns book IDs from manifest_20.txt in file order.
func readGoldenManifest(t *testing.T) []int {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(goldenRoot(t), "manifest_20.txt"))
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var ids []int
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		id, err := strconv.Atoi(line)
		if err != nil {
			t.Fatalf("bad book id in manifest: %q", line)
		}
		ids = append(ids, id)
	}
	return ids
}

// extractBody replicates the splitter / body-cleaning logic from SPEC §2.2–§2.3.
// This is kept here so the canonical test is entirely self-contained.
var re3newlines = regexp.MustCompile(`\n{3,}`)

func extractBody(t *testing.T, raw []byte) string {
	t.Helper()
	text := string(bytes.ReplaceAll(raw, []byte("\r\n"), []byte("\n")))
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")

	const startMarker = "*** START OF THE PROJECT GUTENBERG EBOOK"
	const endMarker = "*** END OF THE PROJECT GUTENBERG EBOOK"

	startIdx := -1
	for i, l := range lines {
		if strings.Contains(l, startMarker) {
			startIdx = i
			break
		}
	}
	endIdx := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.Contains(lines[i], endMarker) {
			endIdx = i
			break
		}
	}
	if startIdx == -1 || endIdx <= startIdx {
		t.Fatalf("markers missing or inverted")
	}

	body := strings.Join(lines[startIdx+1:endIdx], "\n")
	body = strings.TrimPrefix(body, "\uFEFF")

	var cleaned []string
	for _, l := range strings.Split(body, "\n") {
		cleaned = append(cleaned, strings.TrimRight(l, " \t"))
	}
	body = strings.Join(cleaned, "\n")
	body = re3newlines.ReplaceAllString(body, "\n\n")
	body = strings.TrimSpace(body)
	return body + "\n"
}

// ---------------------------------------------------------------------------
// TestCanonicalHash — THE GATE TEST
// ---------------------------------------------------------------------------

// TestCanonicalHash indexes all 20 golden books with --positions enabled against
// each of the three mandatory backends, exports the canonical form via
// SerializeCanonical, SHA-256s the result and compares against
// spec/golden/expected.sha256.
//
// Three backends × one frozen hash = this test is the conformance oracle for
// the whole index layer.
func TestCanonicalHash(t *testing.T) {
	stopwords := loadGoldenStopwords(t)
	bookIDs := readGoldenManifest(t)
	gRoot := goldenRoot(t)

	// Pre-load and tokenize all 20 books once (shared across subtests).
	type bookData struct {
		id     int
		tokens []core.Token
	}
	var allBooks []bookData
	for _, id := range bookIDs {
		raw, err := os.ReadFile(filepath.Join(gRoot, strconv.Itoa(id)+".txt"))
		if err != nil {
			t.Fatalf("read book %d: %v", id, err)
		}
		body := extractBody(t, raw)
		tokens, _, _, _ := core.Tokenize(body, stopwords)
		allBooks = append(allBooks, bookData{id: id, tokens: tokens})
	}

	backends := []struct {
		name    string
		factory func(ws string) (Backend, error)
	}{
		{"json", func(ws string) (Backend, error) { return NewJSONBackend(ws) }},
		{"folder", func(ws string) (Backend, error) { return NewFolderBackend(ws) }},
		{"sqlite", func(ws string) (Backend, error) { return NewSQLiteBackend(ws) }},
	}

	for _, bb := range backends {
		bb := bb
		t.Run(bb.name, func(t *testing.T) {
			ws := t.TempDir()
			b, err := bb.factory(ws)
			if err != nil {
				t.Fatalf("create backend: %v", err)
			}
			defer b.Close()

			// Index every book with positions=true (frozen hash requires this).
			for _, bk := range allBooks {
				if err := b.IndexBook(bk.id, bk.tokens, true); err != nil {
					t.Fatalf("IndexBook %d: %v", bk.id, err)
				}
			}

			entries, err := b.ExportCanonical()
			if err != nil {
				t.Fatalf("ExportCanonical: %v", err)
			}

			canonical, err := SerializeCanonical(entries, true)
			if err != nil {
				t.Fatalf("SerializeCanonical: %v", err)
			}

			sum := sha256.Sum256(canonical)
			got := hex.EncodeToString(sum[:])

			if got != expectedCanonicalSHA256 {
				t.Errorf("canonical SHA-256 mismatch:\n  got:  %s\n  want: %s", got, expectedCanonicalSHA256)
				preview := canonical
				if len(preview) > 1000 {
					preview = preview[:1000]
				}
				t.Logf("canonical preview (first 1000 bytes):\n%s", preview)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// SerializeCanonical property tests (independent of the hash value)
// ---------------------------------------------------------------------------

// TestCanonicalSerialisation verifies the formatting properties of
// SerializeCanonical independently of the golden hash.
func TestCanonicalSerialisation(t *testing.T) {
	entries := map[string]IndexEntry{
		// & must appear literally (no HTML escaping).
		"a&b": {DF: 1, Postings: []Posting{{BookID: 1, TF: 1, Positions: []int{0}}}},
		"abc": {DF: 2, Postings: []Posting{
			{BookID: 1, TF: 2, Positions: []int{1, 3}},
			{BookID: 2, TF: 1, Positions: []int{0}},
		}},
	}

	data, err := SerializeCanonical(entries, true)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	s := string(data)

	// SPEC §7: NO trailing newline.
	if strings.HasSuffix(s, "\n") {
		t.Error("canonical output MUST NOT have a trailing newline (SPEC §7)")
	}
	// SPEC §7: SetEscapeHTML(false) — & must not be escaped.
	if strings.Contains(s, `\u0026`) {
		t.Error("& must not be escaped as \\u0026 — SetEscapeHTML must be false (SPEC §7)")
	}
	if !strings.Contains(s, `a&b`) {
		t.Errorf("& must appear literally; got: %s", s)
	}
	// SPEC §7: no spaces after , or :
	if strings.Contains(s, ": ") || strings.Contains(s, ", ") {
		t.Error("canonical JSON must have no spaces after : or , (SPEC §7)")
	}
	// SPEC §7: keys sorted by UTF-8 byte order.
	// "a&b" → 0x26=38 after 'a'; "abc" → 0x62=98 after 'a'. So "a&b" < "abc".
	posAmp := strings.Index(s, `"a&b"`)
	posAbc := strings.Index(s, `"abc"`)
	if posAmp < 0 || posAbc < 0 || posAmp > posAbc {
		t.Errorf("keys not in UTF-8 byte order: 'a&b' at %d, 'abc' at %d", posAmp, posAbc)
	}
}

// TestCanonicalWithoutPositions verifies the 2-element posting format when
// positions are disabled (SPEC §7: "third element omitted").
func TestCanonicalWithoutPositions(t *testing.T) {
	entries := map[string]IndexEntry{
		"hello": {DF: 1, Postings: []Posting{{BookID: 1, TF: 3}}},
	}
	data, err := SerializeCanonical(entries, false)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	s := string(data)
	// Must have [1,3] with no third bracket.
	if !strings.Contains(s, `[1,3]`) {
		t.Errorf("without positions: expected [1,3] in output, got: %s", s)
	}
	if strings.Contains(s, `[1,3,[`) {
		t.Error("without positions: MUST NOT emit a position array (SPEC §7)")
	}
}

// TestCanonicalKeysAreSorted verifies UTF-8 byte ordering of keys.
func TestCanonicalKeysAreSorted(t *testing.T) {
	entries := map[string]IndexEntry{
		"zebra": {DF: 1, Postings: []Posting{{BookID: 1, TF: 1}}},
		"apple": {DF: 1, Postings: []Posting{{BookID: 2, TF: 1}}},
		"mango": {DF: 1, Postings: []Posting{{BookID: 3, TF: 1}}},
	}
	data, err := SerializeCanonical(entries, false)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	s := string(data)
	pa := strings.Index(s, `"apple"`)
	pm := strings.Index(s, `"mango"`)
	pz := strings.Index(s, `"zebra"`)
	if !(pa < pm && pm < pz) {
		t.Errorf("keys not sorted: apple@%d mango@%d zebra@%d", pa, pm, pz)
	}
}

// TestCanonicalValidJSON verifies the output is valid JSON.
func TestCanonicalValidJSON(t *testing.T) {
	entries := map[string]IndexEntry{
		"foo": {DF: 2, Postings: []Posting{
			{BookID: 1, TF: 3, Positions: []int{0, 5, 12}},
			{BookID: 2, TF: 1, Positions: []int{7}},
		}},
	}
	data, err := SerializeCanonical(entries, true)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		t.Fatalf("canonical output is not valid JSON: %v\noutput: %s", err, data)
	}
}

// TestCanonicalIntegersNoLeadingZeros verifies SPEC §7: no leading zeros, no exponent.
func TestCanonicalIntegersNoLeadingZeros(t *testing.T) {
	entries := map[string]IndexEntry{
		"word": {DF: 1, Postings: []Posting{{BookID: 1000, TF: 10, Positions: []int{100}}}},
	}
	data, err := SerializeCanonical(entries, true)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	s := string(data)
	// "1000" should appear, not "1e3" or "01000".
	if !strings.Contains(s, "1000") {
		t.Errorf("integer 1000 not found as decimal: %s", s)
	}
	if strings.Contains(s, "1e3") || strings.Contains(s, "01000") {
		t.Errorf("integer must have no exponent form or leading zeros: %s", s)
	}
}

// TestCanonicalPostingsSortedByID verifies that postings are ordered by
// ascending book_id regardless of insertion order.
func TestCanonicalPostingsSortedByID(t *testing.T) {
	// Deliberately out of order.
	entries := map[string]IndexEntry{
		"term": {DF: 3, Postings: []Posting{
			{BookID: 30, TF: 1},
			{BookID: 10, TF: 2},
			{BookID: 20, TF: 3},
		}},
	}
	data, err := SerializeCanonical(entries, false)
	if err != nil {
		t.Fatalf("SerializeCanonical: %v", err)
	}
	s := string(data)
	p10 := strings.Index(s, `[10,`)
	p20 := strings.Index(s, `[20,`)
	p30 := strings.Index(s, `[30,`)
	if !(p10 < p20 && p20 < p30) {
		t.Errorf("postings not sorted by ascending id: 10@%d 20@%d 30@%d in %s", p10, p20, p30, s)
	}
}

// BenchmarkCanonicalHash provides a rough timing for the full pipeline.
func BenchmarkCanonicalHash(b *testing.B) {
	stopwords, _ := core.LoadStopwords(filepath.Join("..", "..", "..", "spec", "stopwords_en.txt"))
	gRoot := filepath.Join("..", "..", "..", "spec", "golden")
	data, err := os.ReadFile(filepath.Join(gRoot, "manifest_20.txt"))
	if err != nil {
		b.Skipf("manifest not available: %v", err)
	}

	var bookIDs []int
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		id, _ := strconv.Atoi(line)
		bookIDs = append(bookIDs, id)
	}

	type bt struct {
		id     int
		tokens []core.Token
	}
	var books []bt
	for _, id := range bookIDs {
		raw, err := os.ReadFile(filepath.Join(gRoot, fmt.Sprintf("%d.txt", id)))
		if err != nil {
			b.Skipf("book %d not available", id)
		}
		tokens, _, _, _ := core.Tokenize(string(raw), stopwords)
		books = append(books, bt{id: id, tokens: tokens})
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		ws := b.TempDir()
		be, _ := NewJSONBackend(ws)
		for _, bk := range books {
			_ = be.IndexBook(bk.id, bk.tokens, true)
		}
		entries, _ := be.ExportCanonical()
		_, _ = SerializeCanonical(entries, true)
		_ = be.Close()
	}
}
