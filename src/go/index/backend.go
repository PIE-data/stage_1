// Package index implements the three inverted-index backends defined in SPEC §6.
package index

import "engine/core"

// Posting holds a single (bookID, tf, positions) triple.
// Positions is nil when the index was built without --positions.
type Posting struct {
	BookID    int
	TF        int
	Positions []int
}

// IndexEntry is the in-memory representation of one term's index data.
type IndexEntry struct {
	DF       int
	Postings []Posting
}

// Backend is the common interface all three backends satisfy.
//
// IndexBook adds/merges one book's tokens into the index.
// Query returns matching book IDs (ascending) for the given terms and mode ("and"|"or").
// limit ≤ 0 with limit == 0 returns nothing; limit < 0 is an argument error (caller's responsibility).
// ExportCanonical returns the full index as an in-memory map for §7 serialisation.
// HasPositions reports whether the index was built with positions.
// Close releases any held resources (DB connections, etc.).
type Backend interface {
	IndexBook(bookID int, tokens []core.Token, withPositions bool) error
	Query(terms []string, mode string, limit int) ([]int, error)
	ExportCanonical() (map[string]IndexEntry, error)
	HasPositions() (bool, error)
	Close() error
}
