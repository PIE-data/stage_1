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

// BookTokens is one book's kept tokens, the unit of a batch (SPEC §1.2 `index`).
type BookTokens struct {
	BookID int
	Tokens []core.Token
}

// termStat is one term's tf and positions inside a single book.
type termStat struct {
	tf        int
	positions []int
}

// bookTermStats groups a book's tokens by term.  Positions are kept only
// when the index stores them.
func bookTermStats(tokens []core.Token, withPositions bool) map[string]*termStat {
	stats := make(map[string]*termStat)
	for _, tok := range tokens {
		ts := stats[tok.Value]
		if ts == nil {
			ts = &termStat{}
			stats[tok.Value] = ts
		}
		ts.tf++
		if withPositions {
			ts.positions = append(ts.positions, tok.Position)
		}
	}
	return stats
}

// Backend is the common interface all three backends satisfy.
//
// IndexBook adds/merges one book's tokens into the index.
// IndexBatch does the same for several books as ONE write: one rewrite of the
// JSON file, one rewrite per touched term file, one SQLite transaction with a
// single df refresh (SPEC §6).  IndexBook is IndexBatch with one book.
// Query returns matching book IDs (ascending) for the given terms and mode ("and"|"or").
// limit ≤ 0 with limit == 0 returns nothing; limit < 0 is an argument error (caller's responsibility).
// ExportCanonical returns the full index as an in-memory map for §7 serialisation.
// HasPositions reports whether the index was built with positions.
// Close releases any held resources (DB connections, etc.).
type Backend interface {
	IndexBook(bookID int, tokens []core.Token, withPositions bool) error
	IndexBatch(books []BookTokens, withPositions bool) error
	Query(terms []string, mode string, limit int) ([]int, error)
	ExportCanonical() (map[string]IndexEntry, error)
	HasPositions() (bool, error)
	Close() error
}
