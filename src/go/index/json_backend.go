package index

import (
	"bytes"
	"encoding/json"
	"fmt"
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
//
// The file holds exactly the canonical bytes of SPEC §7 -- the same bytes the
// Python and Node implementations write -- so the three languages store the
// same amount of data and the size measured by E5/E6 is comparable:
//
//	{"<term>":{"df":<n>,"postings":[[<id>,<tf>,[<pos>,...]],...]},...}
//
// Without positions a posting is [<id>,<tf>].

// jsonPosting is one posting.  Positions is nil without --positions.
type jsonPosting struct {
	BookID    int
	TF        int
	Positions []int
}

// jsonEntry is one term.
type jsonEntry struct {
	DF       int
	Postings []jsonPosting
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
	index, err := parseCanonical(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", b.path, err)
	}
	return index, nil
}

func (b *JSONBackend) save(index map[string]jsonEntry) error {
	entries := make(map[string]IndexEntry, len(index))
	withPositions := false
	for term, e := range index {
		postings := make([]Posting, len(e.Postings))
		for i, p := range e.Postings {
			postings[i] = Posting(p)
			withPositions = withPositions || p.Positions != nil
		}
		entries[term] = IndexEntry{DF: e.DF, Postings: postings}
	}
	data, err := SerializeCanonical(entries, withPositions)
	if err != nil {
		return err
	}
	return datalake.WriteAtomically(b.path, data)
}

// parseCanonical reads the canonical form written by save().  It is a small
// hand-written parser, not encoding/json: the file is hundreds of MB at the
// larger tiers and reflection-based decoding of millions of tiny arrays
// would dominate the cost that E8 is meant to measure (the file rewrite).
func parseCanonical(data []byte) (map[string]jsonEntry, error) {
	p := canonParser{data: data}
	index := make(map[string]jsonEntry)
	if err := p.expect('{'); err != nil {
		return nil, err
	}
	if p.peek() == '}' {
		return index, nil
	}
	for {
		term, err := p.str()
		if err != nil {
			return nil, err
		}
		if err := p.lit(`:{"df":`); err != nil {
			return nil, err
		}
		df, err := p.integer()
		if err != nil {
			return nil, err
		}
		if err := p.lit(`,"postings":[`); err != nil {
			return nil, err
		}
		var postings []jsonPosting
		for p.peek() != ']' {
			if len(postings) > 0 {
				if err := p.expect(','); err != nil {
					return nil, err
				}
			}
			if err := p.expect('['); err != nil {
				return nil, err
			}
			var post jsonPosting
			if post.BookID, err = p.integer(); err != nil {
				return nil, err
			}
			if err := p.expect(','); err != nil {
				return nil, err
			}
			if post.TF, err = p.integer(); err != nil {
				return nil, err
			}
			if p.peek() == ',' {
				p.pos++
				if err := p.expect('['); err != nil {
					return nil, err
				}
				post.Positions = make([]int, 0, post.TF)
				for p.peek() != ']' {
					if len(post.Positions) > 0 {
						if err := p.expect(','); err != nil {
							return nil, err
						}
					}
					v, err := p.integer()
					if err != nil {
						return nil, err
					}
					post.Positions = append(post.Positions, v)
				}
				p.pos++ // ']'
			}
			if err := p.expect(']'); err != nil {
				return nil, err
			}
			postings = append(postings, post)
		}
		p.pos++ // ']' of postings
		if err := p.expect('}'); err != nil {
			return nil, err
		}
		index[term] = jsonEntry{DF: df, Postings: postings}
		switch p.peek() {
		case ',':
			p.pos++
		case '}':
			return index, nil
		default:
			return nil, p.fail("',' or '}'")
		}
	}
}

type canonParser struct {
	data []byte
	pos  int
}

func (p *canonParser) peek() byte {
	if p.pos < len(p.data) {
		return p.data[p.pos]
	}
	return 0
}

func (p *canonParser) fail(want string) error {
	return fmt.Errorf("not a canonical index: expected %s at byte %d", want, p.pos)
}

func (p *canonParser) expect(c byte) error {
	if p.peek() != c {
		return p.fail(strconv.QuoteRune(rune(c)))
	}
	p.pos++
	return nil
}

func (p *canonParser) lit(s string) error {
	if !bytes.HasPrefix(p.data[p.pos:], []byte(s)) {
		return p.fail(strconv.Quote(s))
	}
	p.pos += len(s)
	return nil
}

func (p *canonParser) integer() (int, error) {
	start := p.pos
	for p.pos < len(p.data) && p.data[p.pos] >= '0' && p.data[p.pos] <= '9' {
		p.pos++
	}
	if p.pos == start {
		return 0, p.fail("an integer")
	}
	return strconv.Atoi(string(p.data[start:p.pos]))
}

// str reads a JSON string.  Terms rarely contain escapes, so encoding/json
// is used only for those that do.
func (p *canonParser) str() (string, error) {
	if err := p.expect('"'); err != nil {
		return "", err
	}
	start, escaped := p.pos, false
	for p.pos < len(p.data) {
		switch p.data[p.pos] {
		case '\\':
			escaped = true
			p.pos += 2
			continue
		case '"':
			raw := p.data[start:p.pos]
			p.pos++
			if !escaped {
				return string(raw), nil
			}
			var s string
			err := json.Unmarshal(p.data[start-1:p.pos], &s)
			return s, err
		}
		p.pos++
	}
	return "", p.fail("end of string")
}

// ---------- Backend implementation -----------------------------------------

// IndexBook merges one book's tokens into the JSON index.
func (b *JSONBackend) IndexBook(bookID int, tokens []core.Token, withPositions bool) error {
	return b.IndexBatch([]BookTokens{{BookID: bookID, Tokens: tokens}}, withPositions)
}

// IndexBatch merges several books into the JSON index.
// SPEC §6.1: load, merge, atomic rewrite of the whole file -- once per batch,
// not once per book (that would be quadratic in the batch size).
func (b *JSONBackend) IndexBatch(books []BookTokens, withPositions bool) error {
	if len(books) == 0 {
		return nil
	}
	index, err := b.load()
	if err != nil {
		return err
	}

	touched := make(map[string]bool)
	for _, book := range books {
		for term, ts := range bookTermStats(book.Tokens, withPositions) {
			entry := index[term]
			posting := jsonPosting{BookID: book.BookID, TF: ts.tf}
			if withPositions {
				posting.Positions = ts.positions
			}
			n := len(entry.Postings)
			if n == 0 || entry.Postings[n-1].BookID < book.BookID {
				// The common case: a new book with a larger id.  Appending
				// keeps the list sorted without scanning it.
				entry.Postings = append(entry.Postings, posting)
			} else {
				// Re-indexing: replace this book's posting (idempotency).
				// A new slice -- never entry.Postings[:0], which would reuse
				// the backing array of a list still stored in the map.
				filtered := make([]jsonPosting, 0, n+1)
				for _, p := range entry.Postings {
					if p.BookID != book.BookID {
						filtered = append(filtered, p)
					}
				}
				entry.Postings = append(filtered, posting)
				touched[term] = true
			}
			entry.DF = len(entry.Postings)
			index[term] = entry
		}
	}
	for term := range touched {
		postings := index[term].Postings
		sort.Slice(postings, func(i, j int) bool { return postings[i].BookID < postings[j].BookID })
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
