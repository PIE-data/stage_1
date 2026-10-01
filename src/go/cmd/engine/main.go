package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

const SupportedSpecVersion = "1.1.4"

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

func run(args []string) int {
	fs := flag.NewFlagSet("engine", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	var workspace string
	var datalakeLayout string
	var indexBackend string
	var metricsOut string
	var logLevel string
	var seed int

	// Global Flags specified in SPEC §1
	fs.StringVar(&workspace, "workspace", "", "root of datalake/datamarts/control (required)")
	fs.StringVar(&datalakeLayout, "datalake-layout", "time", "time | book | hash")
	fs.StringVar(&indexBackend, "index-backend", "json", "json | folder | sqlite | mongo")
	fs.StringVar(&metricsOut, "metrics-out", "", "append one JSON metrics record per run")
	fs.StringVar(&logLevel, "log-level", "info", "error | warn | info | debug")
	fs.IntVar(&seed, "seed", 42, "seed for any randomised choice")
	
	if err := fs.Parse(args); err != nil {
		return 2
	}
	positionals := fs.Args()
	
	// Issue scope: Only implement "version" for the CLI skeleton
	if len(positionals) != 1 || positionals[0] != "version" || workspace == "" {
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

func main() {
	os.Exit(run(os.Args[1:]))
}
