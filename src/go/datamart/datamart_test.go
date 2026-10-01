package datamart

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

// The exact 10 fixtures from the Python version
var fixturesJSON = `[
  {"name": "standard_header", "header": "Title: Pride and Prejudice\nAuthor: Austen, Jane, 1775-1817\nLanguage: English\nRelease Date: June 1, 1998", "expected": {"title": "Pride and Prejudice", "author": "Austen, Jane", "language": "en", "release_date": "1998-06-01"}},
  {"name": "missing_author", "header": "Title: Anonymous Tales\nLanguage: English\nRelease Date: January 2, 2000", "expected": {"title": "Anonymous Tales", "author": null, "language": "en", "release_date": "2000-01-02"}},
  {"name": "multiline_title", "header": "Title: A Journey\n    Around the World\n\tIn Eighty Days\nAuthor: Jules Verne\nLanguage: English", "expected": {"title": "A Journey Around the World In Eighty Days", "author": "Jules Verne", "language": "en", "release_date": null}},
  {"name": "non_english_header", "header": "Title: Les Misérables\nAuthor: Victor Hugo\nLanguage: French\nRelease Date: March 15, 2006", "expected": {"title": "Les Misérables", "author": "Victor Hugo", "language": "fr", "release_date": "2006-03-15"}},
  {"name": "unparseable_date", "header": "Title: Old Stories\nAuthor: Example Author\nLanguage: English\nRelease Date: sometime in spring", "expected": {"title": "Old Stories", "author": "Example Author", "language": "en", "release_date": null}},
  {"name": "missing_title", "header": "Author: Example Author\nLanguage: Italian", "expected": {"title": "Unknown", "author": "Example Author", "language": "it", "release_date": null}},
  {"name": "field_case_and_whitespace", "header": "tItLe:   A   Short\tStory  \naUtHoR:  Doe,   Jane  \nlAnGuAgE:   ENGLISH  \nrElEaSe DaTe: April 9, 2010", "expected": {"title": "A Short Story", "author": "Doe, Jane", "language": "en", "release_date": "2010-04-09"}},
  {"name": "unmapped_language_and_unknown_field", "header": "Title: Collected Tales\nTranslator: Someone Else\n    This is not part of the title\nAuthor: Example Author\nLanguage: KLINGON", "expected": {"title": "Collected Tales", "author": "Example Author", "language": "klingon", "release_date": null}},
  {"name": "invalid_calendar_date", "header": "Title: Winter Tales\nAuthor: Example Author\nLanguage: German\nRelease Date: February 30, 2020", "expected": {"title": "Winter Tales", "author": "Example Author", "language": "de", "release_date": null}},
  {"name": "date_with_ebook_annotation", "header": "Title: A Sample Book\nAuthor: Example Author\nLanguage: Spanish\nRelease Date: February 29, 2020 [eBook #12345]", "expected": {"title": "A Sample Book", "author": "Example Author", "language": "es", "release_date": "2020-02-29"}}
]`

type Fixture struct {
	Name     string `json:"name"`
	Header   string `json:"header"`
	Expected struct {
		Title       string  `json:"title"`
		Author      *string `json:"author"`
		Language    *string `json:"language"`
		ReleaseDate *string `json:"release_date"`
	} `json:"expected"`
}

func deref(s *string) string {
	if s == nil { return "<nil>" }
	return *s
}

func TestParserFixtures(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	langMap := LoadLanguageMap(root)

	var fixtures []Fixture
	if err := json.Unmarshal([]byte(fixturesJSON), &fixtures); err != nil {
		t.Fatalf("Failed to parse fixtures: %v", err)
	}

	for _, tc := range fixtures {
		t.Run(tc.Name, func(t *testing.T) {
			// Parse Record allows us to parse memory strings instantly
			record := ParseRecord(1, tc.Header, "", "", "", "", langMap)

			if record.Title != tc.Expected.Title {
				t.Errorf("Title: got %q, want %q", record.Title, tc.Expected.Title)
			}
			if deref(record.Author) != deref(tc.Expected.Author) {
				t.Errorf("Author: got %q, want %q", deref(record.Author), deref(tc.Expected.Author))
			}
			if deref(record.Language) != deref(tc.Expected.Language) {
				t.Errorf("Language: got %q, want %q", deref(record.Language), deref(tc.Expected.Language))
			}
			if deref(record.ReleaseDate) != deref(tc.Expected.ReleaseDate) {
				t.Errorf("ReleaseDate: got %q, want %q", deref(record.ReleaseDate), deref(tc.Expected.ReleaseDate))
			}
		})
	}
}

func TestStoreUpsertIdempotent(t *testing.T) {
	tmpDir := t.TempDir()
	store, err := NewStore(tmpDir)
	if err != nil {
		t.Fatalf("Failed to create store: %v", err)
	}
	defer store.Close()

	rec := MetadataRecord{
		BookID: 1, Title: "Test Book", HeaderPath: "header.txt", BodyPath: "body.txt", BodyBytes: 123, SHA256: "abcd", IngestedAt: "2026-01-01T00:00:00Z",
	}

	written, err := store.Upsert([]MetadataRecord{rec}, 10)
	if err != nil || written != 1 {
		t.Errorf("First upsert: got written %d, err %v (want 1, nil)", written, err)
	}

	// SPEC Check: re-running writes nothing!
	written, err = store.Upsert([]MetadataRecord{rec}, 10)
	if err != nil || written != 0 {
		t.Errorf("Second upsert: got written %d, err %v (want 0, nil)", written, err)
	}
}
