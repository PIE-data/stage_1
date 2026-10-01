// SerializeCanonical builds the canonical JSON bytes from a map[term]IndexEntry,
// implementing SPEC §7 exactly. This is the single reference implementation
// shared between all tests and (when the CLI issue lands) the export-canonical command.
//
// Rules (SPEC §7):
//   - JSON, UTF-8, LF, NO trailing newline, NO insignificant whitespace.
//   - Top-level object; keys = terms sorted by UTF-8 byte order.
//   - Value: {"df":<int>,"postings":[[<id>,<tf>,[<pos>,…]],…]}
//   - Without positions: third element of each posting triple is omitted.
//   - Integers: no leading zeros, no exponent form.
//   - encoding/json MUST have SetEscapeHTML(false) — otherwise < > & are mangled.
package index

import (
	"bytes"
	"encoding/json"
	"sort"
	"strconv"
)

// SerializeCanonical serialises entries to the canonical byte representation.
// withPositions controls whether the third element (position array) is emitted.
func SerializeCanonical(entries map[string]IndexEntry, withPositions bool) ([]byte, error) {
	terms := make([]string, 0, len(entries))
	for t := range entries {
		terms = append(terms, t)
	}
	// SPEC §7: sorted by UTF-8 byte order. Go's sort.Strings compares bytes,
	// which is identical to UTF-8 byte order for valid UTF-8 strings.
	sort.Strings(terms)

	var buf bytes.Buffer

	buf.WriteByte('{')
	for i, term := range terms {
		if i > 0 {
			buf.WriteByte(',')
		}

		// Write the JSON-encoded key using a dedicated encoder so that
		// SetEscapeHTML(false) applies and & / < / > are not mangled.
		var keyBuf bytes.Buffer
		keyEnc := json.NewEncoder(&keyBuf)
		keyEnc.SetEscapeHTML(false)
		if err := keyEnc.Encode(term); err != nil {
			return nil, err
		}
		// json.Encoder always appends '\n' — strip it.
		keyBytes := bytes.TrimRight(keyBuf.Bytes(), "\n")
		buf.Write(keyBytes)
		buf.WriteByte(':')

		entry := entries[term]

		buf.WriteString(`{"df":`)
		buf.WriteString(strconv.Itoa(entry.DF))
		buf.WriteString(`,"postings":[`)

		// Postings must be sorted by ascending book_id (SPEC §7).
		postings := make([]Posting, len(entry.Postings))
		copy(postings, entry.Postings)
		sort.Slice(postings, func(a, b int) bool {
			return postings[a].BookID < postings[b].BookID
		})

		for j, p := range postings {
			if j > 0 {
				buf.WriteByte(',')
			}
			buf.WriteByte('[')
			buf.WriteString(strconv.Itoa(p.BookID))
			buf.WriteByte(',')
			buf.WriteString(strconv.Itoa(p.TF))
			if withPositions {
				buf.WriteByte(',')
				buf.WriteByte('[')
				for k, pos := range p.Positions {
					if k > 0 {
						buf.WriteByte(',')
					}
					buf.WriteString(strconv.Itoa(pos))
				}
				buf.WriteByte(']')
			}
			buf.WriteByte(']')
		}

		buf.WriteString(`]}`)
	}
	buf.WriteByte('}')
	// SPEC §7: NO trailing newline.
	return buf.Bytes(), nil
}
