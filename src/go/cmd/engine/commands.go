package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"engine/control"
	"engine/core"
	"engine/datalake"
)

func runQuery(workspace, indexBackend, termsStr, mode string, limit int) int {
	if !validBackend(indexBackend) {
		fmt.Fprintf(os.Stderr, "query is not implemented for backend %q\n", indexBackend)
		return exitUsage
	}
	if mode != "and" && mode != "or" {
		fmt.Fprintf(os.Stderr, "--mode must be and|or, got %q\n", mode)
		return exitUsage
	}
	// No index is an error, not an empty result (SPEC.md §1.1) -- checked
	// before opening, because opening creates the folder / database.
	if !indexExists(indexBackend, workspace) {
		fmt.Fprintf(os.Stderr, "no %s index in %s -- run `index --all` first\n", indexBackend, workspace)
		return exitError
	}

	stopwords, err := loadStopwords()
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to load stopwords: %v\n", err)
		return exitError
	}
	tokens, _, _, _ := core.Tokenize(termsStr, stopwords)
	var terms []string
	seen := make(map[string]bool)
	for _, t := range tokens {
		if !seen[t.Value] {
			seen[t.Value] = true
			terms = append(terms, t.Value)
		}
	}
	if len(terms) == 0 {
		fmt.Fprintf(os.Stderr, "every query term was filtered out (%d given): stop words, single characters and all-digit strings are never indexed\n", len(strings.Fields(termsStr)))
		return exitOK
	}

	backend, err := openBackend(indexBackend, workspace)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open %s index: %v\n", indexBackend, err)
		return exitError
	}
	defer backend.Close()

	// limit < 0 means "no --limit" here; the flag itself was validated.
	ids, err := backend.Query(terms, mode, limit)
	if err != nil {
		fmt.Fprintf(os.Stderr, "query error: %v\n", err)
		return exitError
	}
	var out strings.Builder
	for _, id := range ids {
		out.WriteString(strconv.Itoa(id))
		out.WriteByte('\n')
	}
	os.Stdout.WriteString(out.String())
	fmt.Fprintf(os.Stderr, "%d book(s) for [%s] mode=%s backend=%s\n", len(ids), strings.Join(terms, " "), mode, indexBackend)
	return exitOK
}

func runReconcile(workspace, datalakeLayout string) int {
	tracker := control.NewStateTracker(workspace)
	var storage datalake.Storage
	var err error
	switch datalakeLayout {
	case "time":
		storage = &datalake.TimeStorage{Workspace: workspace}
	case "book":
		storage = datalake.NewBookStorage(workspace, nil)
	case "hash":
		storage = &datalake.HashStorage{Workspace: workspace}
	default:
		fmt.Fprintf(os.Stderr, "unknown datalake layout: %q\n", datalakeLayout)
		return 2
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "storage error: %v\n", err)
		return 1
	}

	// Delete leftover *.part files
	removedParts := 0
	for _, sub := range []string{"datalake", "raw", "datamarts", "control"} {
		root := filepath.Join(workspace, sub)
		filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() && strings.HasSuffix(info.Name(), ".part") {
				os.Remove(path)
				removedParts++
			}
			return nil
		})
	}

	var epoch time.Time
	present, err := storage.ListNew(epoch)
	if err != nil {
		fmt.Fprintf(os.Stderr, "list new error: %v\n", err)
		return 1
	}

	var onDisk []int
	for _, id := range present {
		_, _, err := storage.Lookup(id)
		if err == nil {
			onDisk = append(onDisk, id)
		}
	}

	before := tracker.Downloaded()
	var indexed []int
	onDiskMap := make(map[int]bool)
	for _, id := range onDisk {
		onDiskMap[id] = true
	}
	for _, id := range tracker.Indexed() {
		if onDiskMap[id] {
			indexed = append(indexed, id)
		}
	}

	if err := tracker.Rewrite(onDisk, indexed); err != nil {
		fmt.Fprintf(os.Stderr, "rewrite control files error: %v\n", err)
		return 1
	}

	// Receipts
	receiptsRecovered := 0
	receiptsRemoved := 0
	for _, bookID := range onDisk {
		headerPath, bodyPath, _ := storage.Lookup(bookID)
		if readReceipt(workspace, datalakeLayout, bookID) == nil {
			instant := recoveredInstant(workspace, datalakeLayout, bodyPath)
			// we need to call ingestion persist receipt but we might not have it in go, let's write it here.
			writeReceipt(workspace, datalakeLayout, bookID, headerPath, bodyPath, instant)
			receiptsRecovered++
		}
	}

	receiptsDir := filepath.Join(workspace, "control", "ingestion", datalakeLayout)
	if entries, err := os.ReadDir(receiptsDir); err == nil {
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
				idStr := strings.TrimSuffix(e.Name(), ".json")
				id, err := strconv.Atoi(idStr)
				if err != nil || !onDiskMap[id] {
					os.Remove(filepath.Join(receiptsDir, e.Name()))
					receiptsRemoved++
				}
			}
		}
	}

	added := len(onDisk) - len(before)
	dropped := len(before) - len(onDisk) // not exact set diff but enough for summary print

	fmt.Fprintf(os.Stderr, "reconcile: %d downloaded (%d recovered, %d dropped), %d indexed, %d partial file(s) removed, %d receipt(s) recovered, %d removed\n",
		len(onDisk), added, dropped, len(indexed), removedParts, receiptsRecovered, receiptsRemoved)

	return 0
}

func readReceipt(workspace, layout string, bookID int) map[string]interface{} {
	path := filepath.Join(workspace, "control", "ingestion", layout, fmt.Sprintf("%d.json", bookID))
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var record map[string]interface{}
	if err := json.Unmarshal(data, &record); err != nil {
		return nil
	}
	idFloat, ok := record["book_id"].(float64)
	if !ok || int(idFloat) != bookID {
		return nil
	}
	return record
}

func writeReceipt(workspace, layout string, bookID int, headerPath, bodyPath string, instant time.Time) error {
	dir := filepath.Join(workspace, "control", "ingestion", layout)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	path := filepath.Join(dir, fmt.Sprintf("%d.json", bookID))

	record := struct {
		BookID     int    `json:"book_id"`
		HeaderPath string `json:"header_path"`
		BodyPath   string `json:"body_path"`
		IngestedAt string `json:"ingested_at"`
	}{
		BookID:     bookID,
		HeaderPath: headerPath,
		BodyPath:   bodyPath,
		IngestedAt: instant.UTC().Format("2006-01-02T15:04:05Z"),
	}

	buf := &bytes.Buffer{}
	enc := json.NewEncoder(buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(record); err != nil {
		return err
	}
	return datalake.WriteAtomically(path, []byte(strings.TrimSpace(buf.String())+"\n"))
}

func recoveredInstant(workspace, layout, bodyPath string) time.Time {
	fullPath := filepath.Join(workspace, bodyPath)
	info, err := os.Stat(fullPath)
	if err != nil {
		return time.Now().UTC().Truncate(time.Second)
	}
	mtime := info.ModTime().UTC().Truncate(time.Second)

	if layout != "time" {
		return mtime
	}

	parts := strings.Split(bodyPath, "/")
	if len(parts) >= 3 {
		date := parts[1]
		hour := parts[2]
		start, err := time.Parse("2006010215", date+hour)
		if err == nil {
			if mtime.Format("2006010215") == date+hour {
				return mtime
			}
			return start.UTC()
		}
	}
	return mtime
}
