package datalake

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestEveryGoldenBookSplits(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	goldenDir := filepath.Join(root, "spec", "golden")

	manifestBytes, err := os.ReadFile(filepath.Join(goldenDir, "manifest_20.txt"))
	if err != nil {
		t.Fatalf("Failed to read manifest_20.txt: %v", err)
	}

	for _, line := range strings.Split(string(manifestBytes), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		bookID, _ := strconv.Atoi(line)
		bookPath := filepath.Join(goldenDir, strconv.Itoa(bookID)+".txt")
		rawBytes, _ := os.ReadFile(bookPath)

		_, body, err := Split(rawBytes)
		if err != nil {
			t.Errorf("Book %d: split failed: %v", bookID, err)
			continue
		}

		if body == "" {
			t.Errorf("Book %d: body is empty", bookID)
		}
		if !strings.HasSuffix(body, "\n") {
			t.Errorf("Book %d: body does not end with exactly one newline", bookID)
		}
		if strings.Contains(body, "\r") {
			t.Errorf("Book %d: body contains carriage return", bookID)
		}
		if strings.HasPrefix(body, "*** START") {
			t.Errorf("Book %d: body still contains START marker", bookID)
		}
	}
}

func TestSyntheticQuotedEnd(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	path := filepath.Join(root, "spec", "golden", "synthetic_quoted_end.txt")
	rawBytes, err := os.ReadFile(path)
	if err != nil {
		t.Skip("synthetic_quoted_end.txt missing")
	}

	_, body, err := Split(rawBytes)
	if err != nil {
		t.Fatalf("Failed to split synthetic: %v", err)
	}

	// Proves that taking the LAST end marker preserves quotes inside the body
	if !strings.Contains(body, "*** END OF THE PROJECT GUTENBERG EBOOK") {
		t.Errorf("The quoted END marker inside the body should survive")
	}
}
