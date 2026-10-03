package main

// The pipeline commands: download, split, index, metadata, lookup, scan-new,
// export-canonical, control-step.  SPEC.md §1.2.  This file only wires the
// packages that already exist and are tested on their own (datalake, index,
// datamart, control); the behaviour shared by the three languages -- exit
// codes, what `lookup` prints, the raw cache, the positions rule, receipts --
// is SPEC.md §1.2.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"engine/control"
	"engine/core"
	"engine/datalake"
	"engine/datamart"
	"engine/index"
)

const (
	exitOK       = 0
	exitError    = 1
	exitUsage    = 2
	exitNotFound = 3
	exitLocked   = 4
)

// ---------------------------------------------------------------- helpers

// parseInstant reads --now / --since.  A value without a zone is UTC.
func parseInstant(value string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02T15:04", "2006-01-02"} {
		if t, err := time.Parse(layout, value); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("not ISO8601: %q", value)
}

func makeStorage(layout, workspace string, now *time.Time) (datalake.Storage, error) {
	switch layout {
	case "time":
		return &datalake.TimeStorage{Workspace: workspace, Now: now}, nil
	case "book":
		return datalake.NewBookStorage(workspace, now), nil
	case "hash":
		return &datalake.HashStorage{Workspace: workspace}, nil
	}
	return nil, fmt.Errorf("unknown datalake layout: %q", layout)
}

func validBackend(name string) bool {
	return name == "json" || name == "folder" || name == "sqlite"
}

// indexExists is checked BEFORE opening: the folder and sqlite backends
// create their files on open, so an empty workspace would otherwise answer
// "no results" instead of "nothing indexed" (SPEC.md §1.1).
func indexExists(backend, workspace string) bool {
	marts := filepath.Join(workspace, "datamarts")
	var path string
	switch backend {
	case "json":
		path = filepath.Join(marts, "inverted_index.json")
	case "folder":
		path = filepath.Join(marts, "inverted_index")
	case "sqlite":
		path = filepath.Join(marts, "index.db")
	default:
		return false
	}
	_, err := os.Stat(path)
	return err == nil
}

func openBackend(name, workspace string) (index.Backend, error) {
	switch name {
	case "json":
		return index.NewJSONBackend(workspace)
	case "folder":
		return index.NewFolderBackend(workspace)
	case "sqlite":
		return index.NewSQLiteBackend(workspace)
	}
	return nil, fmt.Errorf("unknown index backend: %q", name)
}

func specFile(name string) string {
	return filepath.Join(datamart.RepoRoot(), "spec", name)
}

func loadStopwords() (map[string]bool, error) {
	return core.LoadStopwords(specFile("stopwords_en.txt"))
}

func readManifest(path string) ([]int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var ids []int
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		id, err := strconv.Atoi(line)
		if err != nil {
			return nil, fmt.Errorf("%s: bad book id %q", path, line)
		}
		ids = append(ids, id)
	}
	return ids, nil
}

func rawPath(workspace string, bookID int) string {
	return filepath.Join(workspace, "raw", fmt.Sprintf("%d.txt", bookID))
}

// ------------------------------------------------------ positions setting
//
// Whether an index holds positions is fixed when it is first built
// (SPEC.md §1.2).  It is recorded in datamarts/index_settings.json, the same
// file and bytes as the Python implementation, so a workspace can be resumed
// by any language.

func settingsPath(workspace string) string {
	return filepath.Join(workspace, "datamarts", "index_settings.json")
}

func readSettings(workspace string) map[string]map[string]bool {
	settings := make(map[string]map[string]bool)
	if data, err := os.ReadFile(settingsPath(workspace)); err == nil {
		_ = json.Unmarshal(data, &settings)
	}
	return settings
}

// indexPositions: nil when this backend's index has no recorded setting.
func indexPositions(workspace, backend string) *bool {
	if entry, ok := readSettings(workspace)[backend]; ok {
		if v, ok := entry["positions"]; ok {
			return &v
		}
	}
	return nil
}

func recordPositions(workspace, backend string, positions bool) error {
	settings := readSettings(workspace)
	settings[backend] = map[string]bool{"positions": positions}
	names := make([]string, 0, len(settings))
	for name := range settings {
		names = append(names, name)
	}
	sort.Strings(names)
	// Python json.dumps(sort_keys=True) layout: ", " and ": " separators.
	parts := make([]string, 0, len(names))
	for _, name := range names {
		key, _ := json.Marshal(name)
		parts = append(parts, fmt.Sprintf(`%s: {"positions": %t}`, key, settings[name]["positions"]))
	}
	if err := os.MkdirAll(filepath.Dir(settingsPath(workspace)), 0755); err != nil {
		return err
	}
	return os.WriteFile(settingsPath(workspace), []byte("{"+strings.Join(parts, ", ")+"}\n"), 0644)
}

// --------------------------------------------------------------- ingestion

// storeBook: artifacts -> receipt -> control append, one instant for all.
// The instant is fixed once, to whole seconds, BEFORE writing, so the time
// layout's date/hour folder and the receipt can never disagree.
func storeBook(workspace, layout string, now *time.Time, tracker *control.StateTracker,
	bookID int, header, body string) error {
	instant := time.Now().UTC().Truncate(time.Second)
	if now != nil {
		instant = now.UTC().Truncate(time.Second)
	}
	storage, err := makeStorage(layout, workspace, &instant)
	if err != nil {
		return err
	}
	headerPath, bodyPath, err := storage.Write(bookID, header, body)
	if err != nil {
		return err
	}
	if err := writeReceipt(workspace, layout, bookID, headerPath, bodyPath, instant); err != nil {
		return err
	}
	return tracker.MarkDownloaded(bookID) // only after artifacts and receipt (SPEC.md §2.4)
}

// fetchOne downloads, caches, splits and stores one book.  It returns the
// failure REASON ("" on success); err is for local failures (disk), which
// are not a property of the book and abort the run.
func fetchOne(workspace, layout string, now *time.Time, tracker *control.StateTracker,
	bookID int, sourceBase string) (string, error) {
	raw, err := datalake.Download(context.Background(), bookID, sourceBase)
	if errors.Is(err, datalake.ErrHTTPNotFound) {
		return "NOT_FOUND", nil
	}
	if err != nil {
		return "DOWNLOAD_ERROR", nil
	}
	// SPEC.md §1.2: the decoded text is cached first, for `split`.
	if err := os.MkdirAll(filepath.Join(workspace, "raw"), 0755); err != nil {
		return "", err
	}
	if err := datalake.WriteAtomically(rawPath(workspace, bookID), raw); err != nil {
		return "", err
	}
	header, body, err := datalake.Split(raw)
	if err != nil {
		return "NO_MARKERS", nil // nothing written to the datalake (SPEC.md §2.2)
	}
	return "", storeBook(workspace, layout, now, tracker, bookID, header, body)
}

// --------------------------------------------------------------- download

func runDownload(workspace, layout string, now *time.Time, bookID int, manifest string,
	workers int, sourceBase string) int {
	if workers < 1 {
		fmt.Fprintln(os.Stderr, "--workers must be >= 1")
		return exitUsage
	}
	var ids []int
	if manifest != "" {
		var err error
		if ids, err = readManifest(manifest); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return exitError
		}
	} else {
		ids = []int{bookID}
	}

	tracker := control.NewStateTracker(workspace)
	seen := make(map[int]bool)
	var todo []int
	for _, id := range ids {
		if !seen[id] && !tracker.IsDownloaded(id) { // I4: no request for a known book
			todo = append(todo, id)
		}
		seen[id] = true
	}

	reasons := make([]string, len(todo))
	errs := make([]error, len(todo))
	// A bounded pool: exactly `workers` requests in flight (SPEC.md §1.2).
	jobs := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				reasons[i], errs[i] = fetchOne(workspace, layout, now, tracker, todo[i], sourceBase)
			}
		}()
	}
	for i := range todo {
		jobs <- i
	}
	close(jobs)
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			fmt.Fprintf(os.Stderr, "book %d: %v\n", todo[i], err)
			return exitError
		}
	}
	// Failures are recorded by this goroutine only, in manifest order.
	failed, downloadError := 0, false
	for i, reason := range reasons {
		if reason == "" {
			continue
		}
		failed++
		downloadError = downloadError || reason == "DOWNLOAD_ERROR"
		if err := tracker.MarkFailed(todo[i], reason, ""); err != nil {
			fmt.Fprintf(os.Stderr, "failed_books.txt: %v\n", err)
			return exitError
		}
	}
	fmt.Fprintf(os.Stderr, "downloaded %d, skipped %d already present, failed %d\n",
		len(todo)-failed, len(ids)-len(todo), failed)
	for i, reason := range reasons {
		if reason != "" {
			fmt.Fprintf(os.Stderr, "  %d\t%s\n", todo[i], reason)
		}
	}
	switch {
	case downloadError:
		return exitError
	case failed > 0:
		return exitNotFound
	}
	return exitOK
}

// ------------------------------------------------------------------ split

func runSplit(workspace, layout string, now *time.Time, bookID int) int {
	raw, err := os.ReadFile(rawPath(workspace, bookID))
	if err != nil {
		fmt.Fprintf(os.Stderr, "no cached raw file for book %d: %s\n", bookID, rawPath(workspace, bookID))
		return exitNotFound
	}
	tracker := control.NewStateTracker(workspace)
	header, body, err := datalake.Split(raw)
	if err != nil {
		tracker.MarkFailed(bookID, "NO_MARKERS", "")
		fmt.Fprintf(os.Stderr, "book %d: markers not found\n", bookID)
		return exitNotFound
	}
	// A re-split is a new ingestion: new instant, new receipt (SPEC.md §1.2).
	if err := storeBook(workspace, layout, now, tracker, bookID, header, body); err != nil {
		fmt.Fprintf(os.Stderr, "book %d: %v\n", bookID, err)
		return exitError
	}
	return exitOK
}

// ------------------------------------------------------------------ index

// indexBooks indexes ids in batches; a batch's ids are appended to
// indexed_books.txt only after the batch is committed (SPEC.md §1.2, I3).
// Returns how many were indexed and the ids missing from the datalake.
func indexBooks(workspace, backendName string, positions bool, storage datalake.Storage,
	tracker *control.StateTracker, ids []int, batchSize int) (int, []int, error) {
	stopwords, err := loadStopwords()
	if err != nil {
		return 0, nil, fmt.Errorf("stop words: %w", err)
	}
	backend, err := openBackend(backendName, workspace)
	if err != nil {
		return 0, nil, err
	}
	defer backend.Close()
	if err := recordPositions(workspace, backendName, positions); err != nil {
		return 0, nil, err
	}

	indexed := 0
	var missing []int
	for start := 0; start < len(ids); start += batchSize {
		end := start + batchSize
		if end > len(ids) {
			end = len(ids)
		}
		var batch []index.BookTokens
		var done []int
		for _, id := range ids[start:end] {
			_, bodyPath, err := storage.Lookup(id)
			if err != nil {
				missing = append(missing, id)
				continue
			}
			body, err := os.ReadFile(filepath.Join(workspace, filepath.FromSlash(bodyPath)))
			if err != nil {
				return indexed, missing, err
			}
			tokens, _, _, _ := core.Tokenize(string(body), stopwords)
			batch = append(batch, index.PrepareBook(id, tokens, positions))
			done = append(done, id)
		}
		if err := backend.IndexBatch(batch, positions); err != nil {
			return indexed, missing, err
		}
		if err := tracker.MarkIndexed(done); err != nil {
			return indexed, missing, err
		}
		indexed += len(done)
	}
	return indexed, missing, nil
}

func runIndex(workspace, layout, backendName string, bookID int, all, positions bool, batchSize int) int {
	if !validBackend(backendName) {
		fmt.Fprintf(os.Stderr, "index is not implemented for backend %q\n", backendName)
		return exitUsage
	}
	if batchSize < 1 {
		fmt.Fprintln(os.Stderr, "--batch-size must be >= 1")
		return exitUsage
	}
	if existing := indexPositions(workspace, backendName); existing != nil && *existing != positions {
		with := "without"
		if *existing {
			with = "with"
		}
		fmt.Fprintf(os.Stderr, "the %s index in %s was built %s --positions; rebuild it in a clean workspace to change that\n",
			backendName, workspace, with)
		return exitUsage
	}

	tracker := control.NewStateTracker(workspace)
	storage, err := makeStorage(layout, workspace, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitUsage
	}
	var ids []int
	if all {
		ids = tracker.ReadyToIndex()
	} else {
		if _, _, err := storage.Lookup(bookID); err != nil {
			fmt.Fprintf(os.Stderr, "book %d is not in the %s datalake\n", bookID, layout)
			return exitNotFound
		}
		ids = []int{bookID}
	}

	indexed, missing, err := indexBooks(workspace, backendName, positions, storage, tracker, ids, batchSize)
	if err != nil {
		fmt.Fprintf(os.Stderr, "index: %v\n", err)
		return exitError
	}
	msg := fmt.Sprintf("indexed %d book(s) into %s", indexed, backendName)
	if len(missing) > 0 {
		msg += fmt.Sprintf(", %d listed but missing from the datalake", len(missing))
	}
	fmt.Fprintln(os.Stderr, msg)
	if len(missing) > 0 {
		return exitNotFound
	}
	return exitOK
}

// ----------------------------------------------------------------- lookup

func runLookup(workspace, layout string, bookID int) int {
	storage, err := makeStorage(layout, workspace, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitUsage
	}
	headerPath, bodyPath, err := storage.Lookup(bookID) // filesystem only (SPEC.md §4.1)
	if err != nil {
		fmt.Fprintf(os.Stderr, "book %d not found in the %s datalake\n", bookID, layout)
		return exitNotFound
	}
	// E2 measures resolving AND reading.
	if _, err := os.ReadFile(filepath.Join(workspace, filepath.FromSlash(bodyPath))); err != nil {
		fmt.Fprintf(os.Stderr, "book %d: %v\n", bookID, err)
		return exitError
	}
	fmt.Printf("%s\t%s\n", headerPath, bodyPath)
	return exitOK
}

// --------------------------------------------------------------- scan-new

func runScanNew(workspace, layout, since string) int {
	from := time.Unix(0, 0).UTC()
	if since != "" {
		t, err := parseInstant(since)
		if err != nil {
			fmt.Fprintf(os.Stderr, "--since is not ISO8601: %q\n", since)
			return exitUsage
		}
		from = t
	}
	storage, err := makeStorage(layout, workspace, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitUsage
	}
	present, err := storage.ListNew(from)
	if err != nil {
		fmt.Fprintf(os.Stderr, "scan-new: %v\n", err)
		return exitError
	}
	tracker := control.NewStateTracker(workspace)
	seen := make(map[int]bool)
	var fresh []int
	for _, id := range present {
		if !seen[id] && !tracker.IsIndexed(id) {
			fresh = append(fresh, id)
		}
		seen[id] = true
	}
	sort.Ints(fresh)
	var out strings.Builder
	for _, id := range fresh {
		out.WriteString(strconv.Itoa(id))
		out.WriteByte('\n')
	}
	os.Stdout.WriteString(out.String())
	return exitOK
}

// ------------------------------------------------------- export-canonical

func runExportCanonical(workspace, backendName, out string) int {
	if !validBackend(backendName) {
		fmt.Fprintf(os.Stderr, "unknown index backend %q\n", backendName)
		return exitUsage
	}
	if !indexExists(backendName, workspace) {
		fmt.Fprintf(os.Stderr, "no %s index in %s\n", backendName, workspace)
		return exitError
	}
	backend, err := openBackend(backendName, workspace)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitError
	}
	defer backend.Close()

	// The canonical form differs with and without positions (SPEC.md §7):
	// it must match how the index was built, not be assumed.
	var positions bool
	if recorded := indexPositions(workspace, backendName); recorded != nil {
		positions = *recorded
	} else if positions, err = backend.HasPositions(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitError
	}
	entries, err := backend.ExportCanonical()
	if err != nil {
		fmt.Fprintf(os.Stderr, "export: %v\n", err)
		return exitError
	}
	data, err := index.SerializeCanonical(entries, positions)
	if err != nil {
		fmt.Fprintf(os.Stderr, "export: %v\n", err)
		return exitError
	}
	if err := os.WriteFile(out, data, 0644); err != nil {
		fmt.Fprintf(os.Stderr, "export: %v\n", err)
		return exitError
	}
	fmt.Fprintf(os.Stderr, "%d bytes -> %s\n", len(data), out)
	return exitOK
}

// --------------------------------------------------------------- metadata

var receiptStamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`)

// readIngestedAt returns the receipt's ingested_at after checking that it
// names this book and these artifacts, at the paths the layout dictates.
// The receipt is the ONLY source of ingested_at (SPEC.md §1.2).
func readIngestedAt(workspace, layout string, bookID int, headerPath, bodyPath string) (string, error) {
	receipt := filepath.Join(workspace, "control", "ingestion", layout, fmt.Sprintf("%d.json", bookID))
	fail := fmt.Errorf("book %d: missing or invalid ingestion receipt: %s", bookID, receipt)
	data, err := os.ReadFile(receipt)
	if err != nil {
		return "", fail
	}
	var r struct {
		BookID     *int    `json:"book_id"`
		HeaderPath *string `json:"header_path"`
		BodyPath   *string `json:"body_path"`
		IngestedAt *string `json:"ingested_at"`
	}
	if json.Unmarshal(data, &r) != nil || r.BookID == nil || *r.BookID != bookID ||
		r.HeaderPath == nil || r.BodyPath == nil || r.IngestedAt == nil {
		return "", fail
	}
	if *r.HeaderPath != headerPath || *r.BodyPath != bodyPath || !receiptStamp.MatchString(*r.IngestedAt) {
		return "", fail
	}
	instant, err := time.Parse("2006-01-02T15:04:05Z", *r.IngestedAt)
	if err != nil {
		return "", fail
	}
	var dir string
	switch layout {
	case "book":
		dir = fmt.Sprintf("datalake/books/%d", bookID)
		if headerPath != dir+"/header.txt" || bodyPath != dir+"/body.txt" {
			return "", fail
		}
		return *r.IngestedAt, nil
	case "hash":
		id6 := fmt.Sprintf("%06d", bookID)
		dir = "datalake/" + id6[0:2] + "/" + id6[2:4]
	case "time":
		dir = "datalake/" + instant.Format("20060102") + "/" + instant.Format("15")
	default:
		return "", fail
	}
	if headerPath != fmt.Sprintf("%s/%d.header.txt", dir, bookID) || bodyPath != fmt.Sprintf("%s/%d.body.txt", dir, bookID) {
		return "", fail
	}
	return *r.IngestedAt, nil
}

// metaJSON: the §5.1 record as one line, keys in schema order, UTF-8 (not
// \uXXXX), then LF -- the bytes of JSON.stringify(record) + "\n" (SPEC.md §4.2).
func metaJSON(record datamart.MetadataRecord) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf) // Encode appends the LF
	enc.SetEscapeHTML(false)
	err := enc.Encode(record)
	return buf.Bytes(), err
}

func runMetadata(workspace, layout string, bookID int, all bool, batchSize int) int {
	if batchSize < 1 {
		fmt.Fprintln(os.Stderr, "--batch-size must be >= 1")
		return exitUsage
	}
	storage, err := makeStorage(layout, workspace, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitUsage
	}
	var ids []int
	if all {
		present, err := storage.ListNew(time.Unix(0, 0).UTC())
		if err != nil {
			fmt.Fprintf(os.Stderr, "metadata: %v\n", err)
			return exitError
		}
		seen := make(map[int]bool)
		for _, id := range present {
			if !seen[id] {
				ids = append(ids, id)
			}
			seen[id] = true
		}
		sort.Ints(ids)
	} else {
		ids = []int{bookID}
	}

	// Validate every input before touching SQLite or meta.json.
	type input struct {
		id                   int
		headerPath, bodyPath string
		stamp                string
	}
	var inputs []input
	for _, id := range ids {
		headerPath, bodyPath, err := storage.Lookup(id)
		if err != nil {
			fmt.Fprintf(os.Stderr, "book %d not found in datalake\n", id)
			return exitNotFound
		}
		stamp, err := readIngestedAt(workspace, layout, id, headerPath, bodyPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return exitError
		}
		inputs = append(inputs, input{id, headerPath, bodyPath, stamp})
	}

	written := 0
	if len(inputs) > 0 {
		store, err := datamart.NewStore(workspace)
		if err != nil {
			fmt.Fprintf(os.Stderr, "metadata: %v\n", err)
			return exitError
		}
		defer store.Close()
		langMap := datamart.LoadLanguageMap(datamart.RepoRoot())
		for start := 0; start < len(inputs); start += batchSize {
			end := start + batchSize
			if end > len(inputs) {
				end = len(inputs)
			}
			records := make([]datamart.MetadataRecord, 0, end-start)
			for _, in := range inputs[start:end] {
				header, err := os.ReadFile(filepath.Join(workspace, filepath.FromSlash(in.headerPath)))
				if err != nil {
					fmt.Fprintf(os.Stderr, "book %d: %v\n", in.id, err)
					return exitError
				}
				body, err := os.ReadFile(filepath.Join(workspace, filepath.FromSlash(in.bodyPath)))
				if err != nil {
					fmt.Fprintf(os.Stderr, "book %d: %v\n", in.id, err)
					return exitError
				}
				records = append(records, datamart.ParseRecord(in.id, string(header), string(body),
					in.headerPath, in.bodyPath, in.stamp, langMap))
			}
			n, err := store.Upsert(records, batchSize)
			if err != nil {
				fmt.Fprintf(os.Stderr, "metadata: %v\n", err)
				return exitError
			}
			written += n
			if layout == "book" {
				// Rewritten only when missing, unreadable or different (SPEC.md §4.2).
				for _, record := range records {
					want, err := metaJSON(record)
					if err != nil {
						fmt.Fprintf(os.Stderr, "book %d: %v\n", record.BookID, err)
						return exitError
					}
					target := filepath.Join(workspace, filepath.FromSlash(path.Dir(record.BodyPath)), "meta.json")
					if have, err := os.ReadFile(target); err == nil && bytes.Equal(have, want) {
						continue
					}
					if err := datalake.WriteAtomically(target, want); err != nil {
						fmt.Fprintf(os.Stderr, "book %d: %v\n", record.BookID, err)
						return exitError
					}
				}
			}
		}
	}
	fmt.Fprintf(os.Stderr, "metadata: processed %d, written %d\n", len(inputs), written)
	return exitOK
}

// ----------------------------------------------------------- control-step

// runControlStep: each iteration moves ONE book one stage forward -- index
// the smallest downloaded-but-not-indexed id, else download the next
// candidate (SPEC.md §1.2).  No randomness: the three languages pick the
// same books in the same order.
func runControlStep(workspace string, iterations, totalBooks int, manifest, sourceBase,
	layout, backendName string, now *time.Time) int {
	if !validBackend(backendName) {
		fmt.Fprintf(os.Stderr, "control-step is not implemented for backend %q\n", backendName)
		return exitUsage
	}
	if iterations < 1 || totalBooks < 1 {
		fmt.Fprintln(os.Stderr, "--iterations and --total-books must be >= 1")
		return exitUsage
	}
	positions := true // word-level unless the index already exists without them
	if recorded := indexPositions(workspace, backendName); recorded != nil {
		positions = *recorded
	}
	var candidates []int
	if manifest != "" {
		var err error
		if candidates, err = readManifest(manifest); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return exitError
		}
	} else {
		for i := 1; i <= totalBooks; i++ {
			candidates = append(candidates, i)
		}
	}
	tracker := control.NewStateTracker(workspace)
	storage, err := makeStorage(layout, workspace, now)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return exitUsage
	}

	downloaded, indexed, failed, cursor := 0, 0, 0, 0
	for i := 0; i < iterations; i++ {
		if pending := tracker.ReadyToIndex(); len(pending) > 0 {
			n, _, err := indexBooks(workspace, backendName, positions, storage, tracker, pending[:1], 1)
			if err != nil {
				fmt.Fprintf(os.Stderr, "control-step: %v\n", err)
				return exitError
			}
			indexed += n
			continue
		}
		bookID, found := 0, false
		for cursor < len(candidates) {
			id := candidates[cursor]
			cursor++
			if !tracker.IsDownloaded(id) && !tracker.IsFailed(id) {
				bookID, found = id, true
				break
			}
		}
		if !found {
			break // nothing left: a complete corpus performs zero writes (I4)
		}
		reason, err := fetchOne(workspace, layout, now, tracker, bookID, sourceBase)
		if err != nil {
			fmt.Fprintf(os.Stderr, "control-step: book %d: %v\n", bookID, err)
			return exitError
		}
		if reason != "" {
			tracker.MarkFailed(bookID, reason, "")
			failed++
		} else {
			downloaded++
		}
	}
	fmt.Fprintf(os.Stderr, "control-step: downloaded %d, indexed %d, failed %d\n", downloaded, indexed, failed)
	return exitOK
}
