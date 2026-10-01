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
)

const SupportedSpecVersion = "1.1.8"

func findSpecVersion() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}

	for {
		candidate := filepath.Join(dir, "spec", "SPEC_VERSION")
		if _, err := os.Stat(candidate); err == nil {
			content, err := os.ReadFile(candidate)
			if err != nil {
				return "", err
			}
			return strings.TrimSpace(string(content)), nil
		}

		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "", fmt.Errorf("spec/SPEC_VERSION not found")
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
	startTime := time.Now()
	startMono := time.Now()
	startedAt := startTime.UTC().Format("2006-01-02T15:04:05.000Z")

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

	version, err := findSpecVersion()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Cannot read SPEC_VERSION: %v\n", err)
		return 1
	}
	if version != SupportedSpecVersion {
		fmt.Fprintf(os.Stderr, "Spec mismatch: supported %s, found %s\n", SupportedSpecVersion, version)
		return 1
	}

	exitCode := 0

	cmdFlags := flag.NewFlagSet(command, flag.ContinueOnError)
	cmdFlags.SetOutput(os.Stderr)
	
	switch command {
	case "reconcile":
		if err := cmdFlags.Parse(positionals[1:]); err != nil {
			return 2
		}
		lck := control.NewWorkspaceLock(workspace)
		if err := lck.Lock(); err != nil {
			return 4
		}
		defer lck.Unlock()
		exitCode = runReconcile(workspace, datalakeLayout)

	case "query":
		var termsStr string
		var mode string
		var limit int
		cmdFlags.StringVar(&termsStr, "terms", "", "terms to search")
		cmdFlags.StringVar(&mode, "mode", "and", "and|or")
		cmdFlags.IntVar(&limit, "limit", 0, "truncate after ordering")
		if err := cmdFlags.Parse(positionals[1:]); err != nil {
			return 2
		}
		if limit < 0 {
			return 2
		}
		exitCode = runQuery(workspace, indexBackend, termsStr, mode, limit)

	case "control-step":
		var iterations int
		var totalBooks int
		var manifest string
		var sourceBase string
		cmdFlags.IntVar(&iterations, "iterations", 1, "number of iterations")
		cmdFlags.IntVar(&totalBooks, "total-books", 70000, "total books")
		cmdFlags.StringVar(&manifest, "manifest", "", "manifest file")
		cmdFlags.StringVar(&sourceBase, "source-base", "", "source URL base")
		if err := cmdFlags.Parse(positionals[1:]); err != nil {
			return 2
		}
		lck := control.NewWorkspaceLock(workspace)
		if err := lck.Lock(); err != nil {
			return 4
		}
		defer lck.Unlock()
		// TODO: Implement control-step logic. The instructions say "Control layer + reconcile...". Is control-step full port required?
		// "The last step of the port... Control layer + reconcile -- invariants I1-I4 pass, including SIGKILL at 50% and resume; reconcile rebuilds the control files after a crash between the rename and the append".
		// I will implement control-step if needed, but the Python reference control_pipeline_step was mentioned to be in core/control_layer.py in phase 1, but then moved to pipeline.py? Let's check.
		exitCode = runControlStep(workspace, iterations, totalBooks, manifest, sourceBase, datalakeLayout, indexBackend, nowOverride)

	// Stub out other commands if not fully implemented in this ticket
	default:
		fmt.Fprintf(os.Stderr, "Unimplemented command: %s\n", command)
		exitCode = 2
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
