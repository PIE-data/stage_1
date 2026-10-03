// Package index implements the three inverted-index backends defined in SPEC §6.
package index

import (
	"strings"

	"engine/core"
)

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

// BookTokens is one book of a batch (SPEC §1.2 `index`).
//
// Build it with PrepareBook, which keeps only the per-term counts and
// positions and drops the token list: a batch holds up to --batch-size books,
// and their raw tokens (one string per word occurrence) took several times
// the memory of the counts.  Tokens is still accepted for callers that build
// the struct directly (IndexBook, tests).
type BookTokens struct {
	BookID int
	Tokens []core.Token
	stats  map[string]*termStat
}

// PrepareBook groups a book's tokens by term right away, so the caller can
// let the tokens go before the next book is read.
func PrepareBook(bookID int, tokens []core.Token, withPositions bool) BookTokens {
	return BookTokens{BookID: bookID, stats: bookTermStats(tokens, withPositions)}
}

// termStats returns the per-term data, computing it if it was not prepared.
func (b BookTokens) termStats(withPositions bool) map[string]*termStat {
	if b.stats != nil {
		return b.stats
	}
	return bookTermStats(b.Tokens, withPositions)
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
			// Clone the key: tok.Value is backed by the tokenizer's builder
			// buffer, which can be larger than the word itself.
			ts = &termStat{}
			stats[strings.Clone(tok.Value)] = ts
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
