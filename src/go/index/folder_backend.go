package index

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"engine/core"
	"engine/datalake"
)

// FolderBackend implements Backend using one file per term.
// SPEC §6.2: datamarts/inverted_index/<BUCKET>/<safe_term>.txt
type FolderBackend struct {
	workspace string
	root      string // datamarts/inverted_index/
}

// NewFolderBackend creates (or opens) the folder backend for the given workspace.
func NewFolderBackend(workspace string) (*FolderBackend, error) {
	root := filepath.Join(workspace, "datamarts", "inverted_index")
	if err := os.MkdirAll(root, 0755); err != nil {
		return nil, err
	}
	return &FolderBackend{workspace: workspace, root: root}, nil
}

// ---------- path helpers ---------------------------------------------------

// termBucket returns the bucket directory name for a term.
// SPEC §6.2: uppercase first code point if A–Z, otherwise _.
// Since all terms are lowercased by the pipeline, the first code point is
// always in [a-z] or non-ASCII, so this always returns "_" for real terms.
// We implement the check faithfully as written in the spec.
func termBucket(term string) string {
	if term == "" {
		return "_"
	}
	runes := []rune(term)
	first := runes[0]
	if first >= 'A' && first <= 'Z' {
		return string(first)
	}
	return "_"
}

// safeTerm encodes every code point outside [a-z0-9] as %XX of its UTF-8 bytes.
// SPEC §6.2: "the term with every code point outside [a-z0-9] percent-encoded
// as %XX of its UTF-8 bytes."
func safeTerm(term string) string {
	var buf strings.Builder
	for _, r := range term {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			buf.WriteRune(r)
		} else {
			// Encode each UTF-8 byte of this code point as %XX.
			encoded := []byte(string(r))
			for _, b := range encoded {
				fmt.Fprintf(&buf, "%%%02X", b)
			}
		}
	}
	return buf.String()
}

// termPath returns the absolute path to the posting file for a term.
func (b *FolderBackend) termPath(term string) string {
	bucket := termBucket(term)
	return filepath.Join(b.root, bucket, safeTerm(term)+".txt")
}

// ---------- load / save a single term file ----------------------------------

type folderPosting struct {
	BookID    int
	TF        int
	Positions []int // nil if not using positions
}

// loadTerm reads the posting file for one term.
// Returns empty slice if the file does not exist.
func loadTerm(path string) ([]folderPosting, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var postings []folderPosting
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "\t", 3)
		if len(parts) < 2 {
			continue
		}
		bookID, err := strconv.Atoi(parts[0])
		if err != nil {
			continue
		}
		tf, err := strconv.Atoi(parts[1])
		if err != nil {
			continue
		}
		p := folderPosting{BookID: bookID, TF: tf}
		if len(parts) == 3 && parts[2] != "" {
			for _, ps := range strings.Split(parts[2], ",") {
				pos, err := strconv.Atoi(ps)
				if err == nil {
					p.Positions = append(p.Positions, pos)
				}
			}
		}
		postings = append(postings, p)
	}
	return postings, scanner.Err()
}

// saveTerm writes the posting list to a file atomically.
// SPEC §6.2: file content, one posting per line: <book_id>\t<tf>\t<pos1>,<pos2>,...\n
// Without positions the third column is omitted.
func saveTerm(path string, postings []folderPosting, withPositions bool) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}

	var buf strings.Builder
	for _, p := range postings {
		buf.WriteString(strconv.Itoa(p.BookID))
		buf.WriteByte('\t')
		buf.WriteString(strconv.Itoa(p.TF))
		if withPositions && len(p.Positions) > 0 {
			buf.WriteByte('\t')
			buf.WriteString(intSliceToString(p.Positions))
		}
		buf.WriteByte('\n')
	}

	return datalake.WriteAtomically(path, []byte(buf.String()))
}

// ---------- Backend implementation -----------------------------------------

// IndexBook merges one book's tokens into the folder index.
// SPEC §6.2: update = read file, merge, atomic rewrite of that file only.
func (b *FolderBackend) IndexBook(bookID int, tokens []core.Token, withPositions bool) error {
	// Build per-term data for this book.
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

	for term, ti := range perTerm {
		path := b.termPath(term)
		postings, err := loadTerm(path)
		if err != nil {
			return err
		}

		// Remove existing posting for this book (idempotency / INSERT OR REPLACE).
		// Use a new nil slice — do NOT do postings[:0] which reuses backing array.
		var filtered []folderPosting
		for _, p := range postings {
			if p.BookID != bookID {
				filtered = append(filtered, p)
			}
		}
		postings = filtered

		newPosting := folderPosting{BookID: bookID, TF: ti.tf}
		if withPositions {
			newPosting.Positions = ti.positions
		}
		postings = append(postings, newPosting)

		// Sort by ascending book_id.
		sort.Slice(postings, func(i, j int) bool {
			return postings[i].BookID < postings[j].BookID
		})

		if err := saveTerm(path, postings, withPositions); err != nil {
			return err
		}
	}
	return nil
}

// Query returns matching book IDs per SPEC §1.1.
func (b *FolderBackend) Query(terms []string, mode string, limit int) ([]int, error) {
	if len(terms) == 0 {
		return nil, nil
	}

	switch mode {
	case "and":
		var sets []map[int]bool
		for _, term := range terms {
			postings, err := loadTerm(b.termPath(term))
			if err != nil {
				return nil, err
			}
			if len(postings) == 0 {
				return nil, nil // missing term → empty AND
			}
			set := make(map[int]bool, len(postings))
			for _, p := range postings {
				set[p.BookID] = true
			}
			sets = append(sets, set)
		}
		result := sets[0]
		for _, s := range sets[1:] {
			for id := range result {
				if !s[id] {
					delete(result, id)
				}
			}
		}
		ids := make([]int, 0, len(result))
		for id := range result {
			ids = append(ids, id)
		}
		return limitAndSort(ids, limit), nil

	case "or":
		seen := make(map[int]bool)
		for _, term := range terms {
			postings, err := loadTerm(b.termPath(term))
			if err != nil {
				return nil, err
			}
			for _, p := range postings {
				seen[p.BookID] = true
			}
		}
		ids := make([]int, 0, len(seen))
		for id := range seen {
			ids = append(ids, id)
		}
		return limitAndSort(ids, limit), nil

	default:
		return nil, fmt.Errorf("unknown mode: %s", mode)
	}
}

// ExportCanonical walks all term files and returns the in-memory map (SPEC §7).
func (b *FolderBackend) ExportCanonical() (map[string]IndexEntry, error) {
	result := make(map[string]IndexEntry)

	err := filepath.WalkDir(b.root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		if !strings.HasSuffix(d.Name(), ".txt") {
			return nil
		}
		// Decode term from safe filename.
		encoded := strings.TrimSuffix(d.Name(), ".txt")
		term, err := decodeSafeTerm(encoded)
		if err != nil {
			return err
		}

		postings, err := loadTerm(path)
		if err != nil {
			return err
		}

		ps := make([]Posting, len(postings))
		for i, p := range postings {
			ps[i] = Posting{BookID: p.BookID, TF: p.TF, Positions: p.Positions}
		}
		result[term] = IndexEntry{DF: len(postings), Postings: ps}
		return nil
	})
	return result, err
}

// decodeSafeTerm reverses the percent-encoding applied by safeTerm.
func decodeSafeTerm(encoded string) (string, error) {
	var buf strings.Builder
	for i := 0; i < len(encoded); {
		if encoded[i] == '%' && i+2 < len(encoded) {
			b, err := strconv.ParseUint(encoded[i+1:i+3], 16, 8)
			if err != nil {
				return "", fmt.Errorf("invalid percent-encoding at %d in %q", i, encoded)
			}
			buf.WriteByte(byte(b))
			i += 3
		} else {
			buf.WriteByte(encoded[i])
			i++
		}
	}
	return buf.String(), nil
}

// HasPositions reports whether the index was built with positions.
func (b *FolderBackend) HasPositions() (bool, error) {
	var found bool
	err := filepath.WalkDir(b.root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || found {
			return nil
		}
		if !strings.HasSuffix(d.Name(), ".txt") {
			return nil
		}
		postings, err := loadTerm(path)
		if err != nil || len(postings) == 0 {
			return nil
		}
		found = postings[0].Positions != nil
		return filepath.SkipAll
	})
	return found, err
}

// Close is a no-op for the folder backend.
func (b *FolderBackend) Close() error { return nil }

// Ensure the interface is satisfied at compile time.
var _ Backend = (*FolderBackend)(nil)
