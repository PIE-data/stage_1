package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"engine/control"
	"engine/core"
	"engine/datalake"
	"engine/index"
)

func fetchOne(workspace, layout string, now *time.Time, tracker *control.StateTracker, bookID int, sourceBase string) *string {
	if sourceBase == "" {
		sourceBase = "https://www.gutenberg.org"
	}

	raw, err := datalake.Download(context.Background(), bookID, sourceBase)
	if err != nil {
		if strings.Contains(err.Error(), "404") { // Simplified check
			r := "NOT_FOUND"
			return &r
		}
		fmt.Fprintf(os.Stderr, "DOWNLOAD ERROR: %v\n", err)
		r := "DOWNLOAD_ERROR"
		return &r
	}

	rawPath := filepath.Join(workspace, "raw", fmt.Sprintf("%d.txt", bookID))
	os.MkdirAll(filepath.Dir(rawPath), 0755)
	datalake.WriteAtomically(rawPath, raw)

	header, body, err := datalake.Split(raw)
	if err != nil { 
		r := "NO_MARKERS"
		return &r
	}

	instant := time.Now().UTC().Truncate(time.Second)
	if now != nil {
		instant = now.UTC().Truncate(time.Second)
	}

	var storage datalake.Storage
	switch layout {
	case "time":
		storage = &datalake.TimeStorage{Workspace: workspace, Now: &instant}
	case "book":
		storage = datalake.NewBookStorage(workspace, &instant)
	case "hash":
		storage = &datalake.HashStorage{Workspace: workspace}
	}

	headerPath, bodyPath, err := storage.Write(bookID, header, body)
	if err == nil {
		writeReceipt(workspace, layout, bookID, headerPath, bodyPath, instant)
		tracker.MarkDownloaded(bookID)
	}
	return nil
}

func runControlStep(workspace string, iterations, totalBooks int, manifest, sourceBase, datalakeLayout, indexBackend, nowOverride string) int {
	if indexBackend != "json" && indexBackend != "folder" && indexBackend != "sqlite" {
		fmt.Fprintf(os.Stderr, "control-step is not implemented for backend %q\n", indexBackend)
		return 2
	}

	var now *time.Time
	if nowOverride != "" {
		t, err := time.Parse(time.RFC3339, nowOverride)
		if err != nil {
			t, err = time.Parse("2006-01-02T15:04:05Z", nowOverride)
			if err != nil {
				fmt.Fprintf(os.Stderr, "--now is not ISO8601: %q\n", nowOverride)
				return 2
			}
		}
		now = &t
	}

	tracker := control.NewStateTracker(workspace)
	var storage datalake.Storage
	switch datalakeLayout {
	case "time":
		storage = &datalake.TimeStorage{Workspace: workspace, Now: now}
	case "book":
		storage = datalake.NewBookStorage(workspace, now)
	case "hash":
		storage = &datalake.HashStorage{Workspace: workspace}
	}

	var candidates []int
	if manifest != "" {
		data, err := os.ReadFile(manifest)
		if err == nil {
			for _, line := range strings.Split(string(data), "\n") {
				line = strings.TrimSpace(line)
				if line != "" && !strings.HasPrefix(line, "#") {
					if id, err := strconv.Atoi(line); err == nil {
						candidates = append(candidates, id)
					}
				}
			}
		}
	} else {
		for i := 1; i <= totalBooks; i++ {
			candidates = append(candidates, i)
		}
	}

	stopwords, _ := core.LoadStopwords(filepath.Join(workspace, "spec", "stopwords_en.txt"))
	if stopwords == nil {
		stopwords, _ = core.LoadStopwords(filepath.Join("spec", "stopwords_en.txt"))
	}

	downloaded, indexed, failed := 0, 0, 0
	cursor := 0

	for i := 0; i < iterations; i++ {
		pending := tracker.ReadyToIndex()
		if len(pending) > 0 {
			bookID := pending[0]
			var backend index.Backend
			switch indexBackend {
			case "json":
				backend, _ = index.NewJSONBackend(workspace)
			case "folder":
				backend, _ = index.NewFolderBackend(workspace)
			case "sqlite":
				backend, _ = index.NewSQLiteBackend(workspace)
			}
			
			_, bodyPath, err := storage.Lookup(bookID)
			if err == nil {
				bodyBytes, _ := os.ReadFile(filepath.Join(workspace, bodyPath))
				tokens, _, _, _ := core.Tokenize(string(bodyBytes), stopwords)
				backend.IndexBook(bookID, tokens, true)
				tracker.MarkIndexed([]int{bookID})
				indexed++
			} else {
				// Missing from datalake
				// Not indexing, just skip or handle differently? Python says `missing.append(book_id)`
				// If missing, we shouldn't mark it indexed. We just continue.
			}
			backend.Close()
			continue
		}

		var bookID int
		found := false
		for cursor < len(candidates) {
			id := candidates[cursor]
			cursor++
			if !tracker.IsDownloaded(id) && !tracker.IsFailed(id) {
				bookID = id
				found = true
				break
			}
		}
		
		if !found {
			break
		}

		reason := fetchOne(workspace, datalakeLayout, now, tracker, bookID, sourceBase)
		if reason != nil {
			tracker.MarkFailed(bookID, *reason, "")
			failed++
		} else {
			downloaded++
		}
	}

	fmt.Fprintf(os.Stderr, "control-step: downloaded %d, indexed %d, failed %d\n", downloaded, indexed, failed)
	return 0
}
