package index

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"

	"engine/core"
	"engine/datalake"
)

// JSONBackend implements Backend using a single monolithic JSON file.
// SPEC §6.1: datamarts/inverted_index.json
// Updating a single book MUST load, merge and rewrite the whole file.
type JSONBackend struct {
	workspace string
	path      string
}

// NewJSONBackend creates (or opens) the JSON backend for the given workspace.
func NewJSONBackend(workspace string) (*JSONBackend, error) {
	path := filepath.Join(workspace, "datamarts", "inverted_index.json")
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, err
	}
	return &JSONBackend{workspace: workspace, path: path}, nil
}

// ---------- on-disk representation ----------------------------------------

// jsonPosting is the wire format for a posting stored in the JSON file.
// Positions is nil when the index was built without --positions.
type jsonPosting struct {
	BookID    int   `json:"book_id"`
	TF        int   `json:"tf"`
	Positions []int `json:"positions,omitempty"`
}

// jsonEntry is the wire format for one term in the JSON file.
type jsonEntry struct {
	DF       int           `json:"df"`
	Postings []jsonPosting `json:"postings"`
	// withPositions is NOT serialised; it is inferred from whether any posting
	// has a non-nil Positions slice.
}

// ---------- load / save ----------------------------------------------------

func (b *JSONBackend) load() (map[string]jsonEntry, error) {
	data, err := os.ReadFile(b.path)
	if os.IsNotExist(err) {
		return make(map[string]jsonEntry), nil
	}
	if err != nil {
		return nil, err
	}
	var index map[string]jsonEntry
	if err := json.Unmarshal(data, &index); err != nil {
		return nil, err
	}
	return index, nil
}

func (b *JSONBackend) save(index map[string]jsonEntry) error {
	data, err := marshalNoEscape(index)
	if err != nil {
		return err
	}
	return datalake.WriteAtomically(b.path, data)
}

// marshalNoEscape serialises v with SetEscapeHTML(false) so that <, >, & are
// not mangled. SPEC §7 requires this; the trap is noted in the issue.
func marshalNoEscape(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	// json.Encoder always appends a newline; strip it (SPEC §6.1 / §7 require no trailing newline).
	out := buf.Bytes()
	if len(out) > 0 && out[len(out)-1] == '\n' {
		out = out[:len(out)-1]
	}
	return out, nil
}

// ---------- Backend implementation -----------------------------------------

// IndexBook merges one book's tokens into the JSON index.
// SPEC §6.1: load, merge, atomic rewrite of the whole file.
func (b *JSONBackend) IndexBook(bookID int, tokens []core.Token, withPositions bool) error {
	index, err := b.load()
	if err != nil {
		return err
	}

	// Build term → {tf, positions} for this book.
	type termInfo struct {
		tf        int
		positions []int
	}
	perTerm := make(map[string]*termInfo)
	for _, tok := range tokens {
		ti := perTerm[tok.Value]
		if ti == nil {
			ti = &termInfo{}
			perTerm[tok.Value] = ti
		}
		ti.tf++
		if withPositions {
			ti.positions = append(ti.positions, tok.Position)
		}
	}

	// Merge into the index, replacing any existing posting for this book.
	for term, ti := range perTerm {
		entry := index[term]

		// Remove existing posting for this book (idempotency / INSERT OR REPLACE).
		// Do NOT use entry.Postings[:0] — it reuses the backing array and can
		// corrupt postings already stored in the map.
		var filtered []jsonPosting
		for _, p := range entry.Postings {
			if p.BookID != bookID {
				filtered = append(filtered, p)
			}
		}
		entry.Postings = filtered

		newPosting := jsonPosting{BookID: bookID, TF: ti.tf}
		if withPositions {
			newPosting.Positions = ti.positions
		}
		entry.Postings = append(entry.Postings, newPosting)

		// Keep sorted by book_id.
		sort.Slice(entry.Postings, func(i, j int) bool {
			return entry.Postings[i].BookID < entry.Postings[j].BookID
		})
		entry.DF = len(entry.Postings)
		index[term] = entry
	}

	return b.save(index)
}

// Query returns matching book IDs per SPEC §1.1.
// limit == 0 → empty result; limit < 0 caller should reject before calling.
func (b *JSONBackend) Query(terms []string, mode string, limit int) ([]int, error) {
	if len(terms) == 0 {
		return nil, nil
	}

	index, err := b.load()
	if err != nil {
		return nil, err
	}

	var result []int
	switch mode {
	case "and":
		result = andQuery(index, terms)
	case "or":
		result = orQuery(index, terms)
	default:
		return nil, nil
	}

	sort.Ints(result)

	if limit == 0 {
		return nil, nil
	}
	if limit > 0 && len(result) > limit {
		result = result[:limit]
	}
	return result, nil
}

func andQuery(index map[string]jsonEntry, terms []string) []int {
	// Intersect: only books present in ALL terms' posting lists.
	// Start with the smallest posting list.
	if len(terms) == 0 {
		return nil
	}
	sets := make([]map[int]bool, 0, len(terms))
	for _, term := range terms {
		e, ok := index[term]
		if !ok {
			return nil // absent term → empty intersection
		}
		set := make(map[int]bool, len(e.Postings))
		for _, p := range e.Postings {
			set[p.BookID] = true
		}
		sets = append(sets, set)
	}
	// Intersect
	result := sets[0]
	for _, s := range sets[1:] {
		for id := range result {
			if !s[id] {
				delete(result, id)
			}
		}
	}
	out := make([]int, 0, len(result))
	for id := range result {
		out = append(out, id)
	}
	return out
}

func orQuery(index map[string]jsonEntry, terms []string) []int {
	seen := make(map[int]bool)
	for _, term := range terms {
		e, ok := index[term]
		if !ok {
			continue
		}
		for _, p := range e.Postings {
			seen[p.BookID] = true
		}
	}
	out := make([]int, 0, len(seen))
	for id := range seen {
		out = append(out, id)
	}
	return out
}

// ExportCanonical returns the full index as an in-memory map (SPEC §7).
func (b *JSONBackend) ExportCanonical() (map[string]IndexEntry, error) {
	raw, err := b.load()
	if err != nil {
		return nil, err
	}
	result := make(map[string]IndexEntry, len(raw))
	for term, entry := range raw {
		postings := make([]Posting, len(entry.Postings))
		for i, p := range entry.Postings {
			postings[i] = Posting{
				BookID:    p.BookID,
				TF:        p.TF,
				Positions: p.Positions,
			}
		}
		result[term] = IndexEntry{DF: entry.DF, Postings: postings}
	}
	return result, nil
}

// HasPositions reports whether the index was built with positions.
func (b *JSONBackend) HasPositions() (bool, error) {
	index, err := b.load()
	if err != nil {
		return false, err
	}
	for _, entry := range index {
		for _, p := range entry.Postings {
			if p.Positions != nil {
				return true, nil
			}
		}
		// found at least one term
		return false, nil
	}
	return false, nil // empty index
}

// Close is a no-op for the JSON backend.
func (b *JSONBackend) Close() error { return nil }

// Ensure the interface is satisfied at compile time.
var _ Backend = (*JSONBackend)(nil)

// ---------- helpers for other backends -------------------------------------

// limitAndSort sorts ids ascending and applies the limit.
// limit == 0 returns nil; limit < 0 returns all (no truncation).
func limitAndSort(ids []int, limit int) []int {
	if limit == 0 {
		return nil
	}
	sort.Ints(ids)
	if limit > 0 && len(ids) > limit {
		ids = ids[:limit]
	}
	return ids
}

// intSliceToString converts a []int to a comma-separated string.
func intSliceToString(positions []int) string {
	if len(positions) == 0 {
		return ""
	}
	parts := make([]string, len(positions))
	for i, p := range positions {
		parts[i] = strconv.Itoa(p)
	}
	return joinStrings(parts, ",")
}

// joinStrings joins with separator (avoids importing strings in backend files).
func joinStrings(parts []string, sep string) string {
	if len(parts) == 0 {
		return ""
	}
	n := len(sep) * (len(parts) - 1)
	for _, p := range parts {
		n += len(p)
	}
	buf := make([]byte, 0, n)
	for i, p := range parts {
		if i > 0 {
			buf = append(buf, sep...)
		}
		buf = append(buf, p...)
	}
	return string(buf)
}
