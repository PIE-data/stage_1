package core

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

type expectedRecord struct {
	BookID       int    `json:"book_id"`
	NTokensRaw   int    `json:"n_tokens_raw"`
	NTokensKept  int    `json:"n_tokens_kept"`
	Sha256Tokens string `json:"sha256_tokens"`
}

func extractGoldenBody(t *testing.T, rawText string) string {
	text := strings.ReplaceAll(rawText, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")

	startMarker := "*** START OF THE PROJECT GUTENBERG EBOOK"
	endMarker := "*** END OF THE PROJECT GUTENBERG EBOOK"

	startIndex := -1
	for i, line := range lines {
		if strings.Contains(line, startMarker) {
			startIndex = i
			break
		}
	}

	endIndex := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.Contains(lines[i], endMarker) {
			endIndex = i
			break
		}
	}

	if startIndex == -1 {
		t.Fatalf("START marker is missing")
	}
	if endIndex <= startIndex {
		t.Fatalf("END marker must follow START")
	}

	bodyLines := lines[startIndex+1 : endIndex]
	body := strings.Join(bodyLines, "\n")

	body = strings.TrimPrefix(body, "\uFEFF")
	
	var cleanedLines []string
	for _, line := range strings.Split(body, "\n") {
		cleanedLine := strings.TrimRight(line, " \t")
		cleanedLines = append(cleanedLines, cleanedLine)
	}
	body = strings.Join(cleanedLines, "\n")

	re := regexp.MustCompile(`\n{3,}`)
	body = re.ReplaceAllString(body, "\n\n")

	body = strings.TrimSpace(body)

	return body + "\n"
}

func TestTokenizerGolden(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	goldenDir := filepath.Join(root, "spec", "golden")

	stopwordsPath := filepath.Join(root, "spec", "stopwords_en.txt")
	stopwords, err := LoadStopwords(stopwordsPath)
	if err != nil {
		t.Fatalf("Failed to load stopwords: %v", err)
	}

	jsonlBytes, err := os.ReadFile(filepath.Join(goldenDir, "tokens_20.jsonl"))
	if err != nil {
		t.Fatalf("Failed to read tokens_20.jsonl: %v", err)
	}
	
	expectedById := make(map[int]expectedRecord)
	scanner := bufio.NewScanner(strings.NewReader(string(jsonlBytes)))
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		var record expectedRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("Failed to parse jsonl line: %v", err)
		}
		expectedById[record.BookID] = record
	}

	manifestBytes, err := os.ReadFile(filepath.Join(goldenDir, "manifest_20.txt"))
	if err != nil {
		t.Fatalf("Failed to read manifest_20.txt: %v", err)
	}
	
	for _, line := range strings.Split(string(manifestBytes), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		bookID, err := strconv.Atoi(line)
		if err != nil {
			t.Fatalf("Invalid book ID in manifest: %v", err)
		}

		expected, ok := expectedById[bookID]
		if !ok {
			t.Fatalf("Missing expected record for book %d", bookID)
		}

		bookPath := filepath.Join(goldenDir, strconv.Itoa(bookID)+".txt")
		rawBytes, err := os.ReadFile(bookPath)
		if err != nil {
			t.Fatalf("Failed to read book %d: %v", bookID, err)
		}

		body := extractGoldenBody(t, string(rawBytes))

		_, nTokensRaw, nTokensKept, sha256Tokens := Tokenize(body, stopwords)

		if nTokensRaw != expected.NTokensRaw {
			t.Errorf("Book %d: expected %d raw tokens, got %d", bookID, expected.NTokensRaw, nTokensRaw)
		}
		if nTokensKept != expected.NTokensKept {
			t.Errorf("Book %d: expected %d kept tokens, got %d", bookID, expected.NTokensKept, nTokensKept)
		}
		if sha256Tokens != expected.Sha256Tokens {
			t.Errorf("Book %d: expected sha256 %s, got %s", bookID, expected.Sha256Tokens, sha256Tokens)
		}
	}
}
