package main

import (
	"testing"
)

func TestVersionCommand(t *testing.T) {
	code := run([]string{"--workspace", ".", "version"})
	if code != 0 {
		t.Errorf("Expected exit code 0, got %d", code)
	}
}

func TestMissingWorkspace(t *testing.T) {
	code := run([]string{"version"})
	if code != 2 {
		t.Errorf("Expected exit code 2 for missing workspace, got %d", code)
	}
}

func TestUnknownCommand(t *testing.T) {
	code := run([]string{"--workspace", ".", "unknown"})
	if code != 2 {
		t.Errorf("Expected exit code 2 for unknown command, got %d", code)
	}
}
