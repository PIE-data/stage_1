package datalake

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStorageLayouts(t *testing.T) {
	now := time.Date(2026, 9, 21, 14, 30, 0, 0, time.UTC)
	bookID := 1342
	header := "Title: Pride and Prejudice\nAuthor: Jane Austen\n"
	body := "It is a truth universally acknowledged...\n"

	expectedPaths := map[string][]string{
		"time": {"datalake/20260921/14/1342.header.txt", "datalake/20260921/14/1342.body.txt"},
		"book": {"datalake/books/1342/header.txt", "datalake/books/1342/body.txt"},
		"hash": {"datalake/00/13/1342.header.txt", "datalake/00/13/1342.body.txt"},
	}

	// Using a slice ensures stable ordering, and we create the Storage inside the loop
	for _, name := range []string{"time", "book", "hash"} {
		t.Run(name, func(t *testing.T) {
			tempDir := t.TempDir() // Gives each layout its own clean workspace!

			var storage Storage
			if name == "time" {
				storage = &TimeStorage{Workspace: tempDir, Now: &now}
			} else if name == "book" {
				storage = &BookStorage{Workspace: tempDir}
			} else {
				storage = &HashStorage{Workspace: tempDir}
			}

			// Write
			hPath, bPath, err := storage.Write(bookID, header, body)
			if err != nil {
				t.Fatalf("Write failed: %v", err)
			}
			if hPath != expectedPaths[name][0] || bPath != expectedPaths[name][1] {
				t.Errorf("Write paths mismatch: got %v, %v", hPath, bPath)
			}

			// Lookup
			hFound, bFound, err := storage.Lookup(bookID)
			if err != nil {
				t.Fatalf("Lookup failed: %v", err)
			}
			if hFound != hPath || bFound != bPath {
				t.Errorf("Lookup paths mismatch: got %v, %v", hFound, bFound)
			}

			// Round-trip identical bytes
			hBytes, _ := os.ReadFile(filepath.Join(tempDir, hFound))
			bBytes, _ := os.ReadFile(filepath.Join(tempDir, bFound))
			if string(hBytes) != header || string(bBytes) != body {
				t.Errorf("Round-trip bytes mismatch")
			}

			// Lookup missing returns ErrNotFound
			if _, _, err := storage.Lookup(99999); err != ErrNotFound {
				t.Errorf("Expected ErrNotFound, got %v", err)
			}

			// ListNew filters by modification time or directory
			since := now.Add(-1 * time.Hour)
			newBooks, err := storage.ListNew(since)
			if err != nil || len(newBooks) != 1 || newBooks[0] != bookID {
				t.Errorf("ListNew failed: %v, got %v", err, newBooks)
			}
		})
	}
}
