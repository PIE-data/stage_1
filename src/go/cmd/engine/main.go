package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"engine/control"
	"engine/datamart"
)

const SupportedSpecVersion = "1.1.9"

const datalakeDefaultSource = "https://www.gutenberg.org"

func findSpecVersion() (string, error) {
	root := datamart.RepoRoot()
	if root == "" {
		return "", fmt.Errorf("spec/SPEC_VERSION not found")
	}
	content, err := os.ReadFile(filepath.Join(root, "spec", "SPEC_VERSION"))
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(content)), nil
}

func getEnvOrNil(key string) *string {
	if val, ok := os.LookupEnv(key); ok {
		return &val
	}
	return nil
}

func getEnvIntOrNil(key string) *int {
	if val, ok := os.LookupEnv(key); ok {
		if i, err := strconv.Atoi(val); err == nil {
			return &i
		}
	}
	return nil
}

func run(args []string) int {
	fs := flag.NewFlagSet("engine", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var workspace string
	var datalakeLayout string
	var indexBackend string
	var metricsOut string
	var logLevel string
	var seed int
	var nowOverride string

	fs.StringVar(&workspace, "workspace", "", "root of datalake/datamarts/control (required)")
	fs.StringVar(&datalakeLayout, "datalake-layout", "time", "time | book | hash")
	fs.StringVar(&indexBackend, "index-backend", "json", "json | folder | sqlite | mongo")
	fs.StringVar(&metricsOut, "metrics-out", "", "append one JSON metrics record per run")
	fs.StringVar(&logLevel, "log-level", "info", "error | warn | info | debug")
	fs.IntVar(&seed, "seed", 42, "seed for any randomised choice")
	fs.StringVar(&nowOverride, "now", "", "override the ingestion instant")

	// Since we are parsing global flags and also command flags, we need a custom parser or just flag.Parse.
	// We'll extract command and parse again for commands if needed.
	// But `flag.Parse` stops at the first non-flag argument (the command).
	if err := fs.Parse(args); err != nil {
		return 2
	}
	positionals := fs.Args()

	if len(positionals) == 0 {
		fmt.Fprintln(os.Stderr, "Usage: engine [GLOBAL FLAGS] <command> [COMMAND FLAGS]")
		return 2
	}
	command := positionals[0]

	if command == "version" {
		if workspace == "" {
			fmt.Fprintln(os.Stderr, "Usage: engine --workspace <path> version")
			return 2
		}
		version, err := findSpecVersion()
		if err != nil {
			fmt.Fprintf(os.Stderr, "Cannot read SPEC_VERSION: %v\n", err)
			return 1
		}
		if version != SupportedSpecVersion {
			fmt.Fprintf(os.Stderr, "Spec mismatch: supported %s, found %s\n", SupportedSpecVersion, version)
			return 1
		}
		fmt.Println(version)
		fmt.Fprintf(os.Stderr, "stage-1-go 0.1.0 | %s\n", runtime.Version())
		return 0
	}

	if workspace == "" {
		fmt.Fprintln(os.Stderr, "--workspace is required")
		return 2
	}
	switch datalakeLayout {
	case "time", "book", "hash":
	default:
		fmt.Fprintf(os.Stderr, "--datalake-layout must be time|book|hash, got %q\n", datalakeLayout)
		return exitUsage
	}
	switch indexBackend {
	case "json", "folder", "sqlite", "mongo":
	default:
		fmt.Fprintf(os.Stderr, "--index-backend must be json|folder|sqlite|mongo, got %q\n", indexBackend)
		return exitUsage
	}

	version, err := findSpecVersion()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Cannot read SPEC_VERSION: %v\n", err)
		return 1
	}
	if version != SupportedSpecVersion {
		fmt.Fprintf(os.Stderr, "Spec mismatch: supported %s, found %s\n", SupportedSpecVersion, version)
		return 1
	}

	var now *time.Time
	if nowOverride != "" {
		t, err := parseInstant(nowOverride)
		if err != nil {
			fmt.Fprintf(os.Stderr, "--now is not ISO8601: %q\n", nowOverride)
			return exitUsage
		}
		now = &t
	}

	cmdFlags := flag.NewFlagSet(command, flag.ContinueOnError)
	cmdFlags.SetOutput(os.Stderr)
	var (
		bookID     int
		manifest   string
		workers    int
		sourceBase string
		all        bool
		positions  bool
		batchSize  int
		termsStr   string
		mode       string
		limit      int
		since      string
		out        string
		iterations int
		totalBooks int
	)
	// The command flags of SPEC.md §1, declared per command so that an
	// unknown flag is an argument error (exit 2), as in the other languages.
	switch command {
	case "download":
		cmdFlags.IntVar(&bookID, "book-id", -1, "one book")
		cmdFlags.StringVar(&manifest, "manifest", "", "file of book ids")
		cmdFlags.IntVar(&workers, "workers", 1, "requests in flight")
		cmdFlags.StringVar(&sourceBase, "source-base", datalakeDefaultSource, "mirror base URL")
	case "split", "lookup":
		cmdFlags.IntVar(&bookID, "book-id", -1, "one book")
	case "index", "metadata":
		cmdFlags.IntVar(&bookID, "book-id", -1, "one book")
		cmdFlags.BoolVar(&all, "all", false, "every pending book")
		cmdFlags.IntVar(&batchSize, "batch-size", 500, "books per committed batch")
		if command == "index" {
			cmdFlags.BoolVar(&positions, "positions", false, "store token positions")
		}
	case "query":
		cmdFlags.StringVar(&termsStr, "terms", "", "space-separated terms")
		cmdFlags.StringVar(&mode, "mode", "", "and|or")
		cmdFlags.IntVar(&limit, "limit", -1, "truncate after ordering")
	case "scan-new":
		cmdFlags.StringVar(&since, "since", "", "ISO8601 instant")
	case "export-canonical":
		cmdFlags.StringVar(&out, "out", "", "output path")
	case "control-step":
		cmdFlags.IntVar(&iterations, "iterations", 0, "number of iterations")
		cmdFlags.IntVar(&totalBooks, "total-books", 70000, "candidate ids 1..N")
		cmdFlags.StringVar(&manifest, "manifest", "", "candidate ids, in order")
		cmdFlags.StringVar(&sourceBase, "source-base", datalakeDefaultSource, "mirror base URL")
	case "reconcile":
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", command)
		return exitUsage
	}
	if err := cmdFlags.Parse(positionals[1:]); err != nil {
		return exitUsage
	}
	if cmdFlags.NArg() > 0 {
		fmt.Fprintf(os.Stderr, "unexpected arguments: %v\n", cmdFlags.Args())
		return exitUsage
	}
	set := make(map[string]bool)
	cmdFlags.Visit(func(f *flag.Flag) { set[f.Name] = true })

	// Exactly one of --book-id / --manifest|--all where SPEC.md §1 says so.
	usage := ""
	switch command {
	case "download":
		if set["book-id"] == (manifest != "") {
			usage = "download needs exactly one of --book-id, --manifest"
		}
	case "index", "metadata":
		if set["book-id"] == all {
			usage = command + " needs exactly one of --book-id, --all"
		}
	case "split", "lookup":
		if !set["book-id"] {
			usage = command + " needs --book-id"
		}
	case "query":
		if !set["terms"] || !set["mode"] {
			usage = "query needs --terms and --mode"
		} else if set["limit"] && limit < 0 {
			usage = "--limit must be >= 0"
		}
	case "export-canonical":
		if out == "" {
			usage = "export-canonical needs --out"
		}
	case "control-step":
		if !set["iterations"] {
			usage = "control-step needs --iterations"
		}
	}
	if usage != "" {
		fmt.Fprintln(os.Stderr, usage)
		return exitUsage
	}

	// Writers hold control/run.lock for the whole run (SPEC.md §1.2).
	switch command {
	case "download", "split", "index", "metadata", "control-step", "reconcile":
		lck := control.NewWorkspaceLock(workspace)
		if err := lck.Lock(); err != nil {
			fmt.Fprintf(os.Stderr, "workspace locked: %v\n", err)
			return exitLocked
		}
		defer lck.Unlock()
	}

	// The clock covers the command only, not parsing or the lock.
	startMono := time.Now()
	startedAt := startMono.UTC().Format("2006-01-02T15:04:05.000Z")
	exitCode := exitOK
	switch command {
	case "download":
		exitCode = runDownload(workspace, datalakeLayout, now, bookID, manifest, workers, sourceBase)
	case "split":
		exitCode = runSplit(workspace, datalakeLayout, now, bookID)
	case "index":
		exitCode = runIndex(workspace, datalakeLayout, indexBackend, bookID, all, positions, batchSize)
	case "metadata":
		exitCode = runMetadata(workspace, datalakeLayout, bookID, all, batchSize)
	case "lookup":
		exitCode = runLookup(workspace, datalakeLayout, bookID)
	case "scan-new":
		exitCode = runScanNew(workspace, datalakeLayout, since)
	case "query":
		exitCode = runQuery(workspace, indexBackend, termsStr, mode, limit)
	case "export-canonical":
		exitCode = runExportCanonical(workspace, indexBackend, out)
	case "control-step":
		exitCode = runControlStep(workspace, iterations, totalBooks, manifest, sourceBase,
			datalakeLayout, indexBackend, now)
	case "reconcile":
		exitCode = runReconcile(workspace, datalakeLayout)
	}

	wallTimeMs := float64(time.Since(startMono).Nanoseconds()) / 1e6

	if metricsOut != "" {
		writeMetrics(metricsOut, startedAt, wallTimeMs, command, datalakeLayout, indexBackend)
	}

	return exitCode
}

func main() {
	os.Exit(run(os.Args[1:]))
}
