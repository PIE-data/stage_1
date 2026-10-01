package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"os/exec"
	"time"
)

func lines(ws, name string) []string {
	content, err := os.ReadFile(filepath.Join(ws, "control", name))
	if err != nil {
		return nil
	}
	var res []string
	for _, l := range strings.Split(string(content), "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			res = append(res, l)
		}
	}
	return res
}

func setupMirror() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimSuffix(filepath.Base(r.URL.Path), "-0.txt")
		// serve dummy content
		content := fmt.Sprintf("Title: Book %s\nAuthor: Author %s\nLanguage: English\n\n*** START OF THE PROJECT GUTENBERG EBOOK ***\nBody %s\n*** END OF THE PROJECT GUTENBERG EBOOK ***\n", id, id, id)
		w.Header().Set("Content-Length", strconv.Itoa(len(content)))
		w.WriteHeader(200)
		w.Write([]byte(content))
	}))
}

func TestControlStep_CompletesCorpus(t *testing.T) {
	mirror := setupMirror()
	defer mirror.Close()

	ws := t.TempDir()
	manifest := filepath.Join(ws, "manifest.txt")
	os.WriteFile(manifest, []byte("10\n11\n"), 0644)

	code := run([]string{
		"--workspace", ws,
		"--datalake-layout", "hash",
		"--index-backend", "json",
		"control-step",
		"--iterations", "10",
		"--manifest", manifest,
		"--source-base", mirror.URL,
	})

	if code != 0 {
		t.Fatalf("Expected exit code 0, got %d", code)
	}

	dl := lines(ws, "downloaded_books.txt")
	if len(dl) != 2 || dl[0] != "10" || dl[1] != "11" {
		t.Errorf("Expected [10, 11] downloaded, got %v", dl)
	}

	idx := lines(ws, "indexed_books.txt")
	if len(idx) != 2 || idx[0] != "10" || idx[1] != "11" {
		t.Errorf("Expected [10, 11] indexed, got %v", idx)
	}
}

func TestReconcile_RepairsControlFiles(t *testing.T) {
	mirror := setupMirror()
	defer mirror.Close()

	ws := t.TempDir()
	manifest := filepath.Join(ws, "manifest.txt")
	os.WriteFile(manifest, []byte("10\n"), 0644)

	run([]string{
		"--workspace", ws,
		"--datalake-layout", "hash",
		"--index-backend", "json",
		"control-step",
		"--iterations", "2",
		"--manifest", manifest,
		"--source-base", mirror.URL,
	})

	// simulate crash/corruption
	os.WriteFile(filepath.Join(ws, "control", "downloaded_books.txt"), []byte("10\n999\n"), 0644)
	
	// test reconcile
	code := run([]string{
		"--workspace", ws,
		"--datalake-layout", "hash",
		"--index-backend", "json",
		"reconcile",
	})

	if code != 0 {
		t.Fatalf("Expected exit code 0, got %d", code)
	}

	dl := lines(ws, "downloaded_books.txt")
	// 999 should be removed because it's not in datalake
	if len(dl) != 1 || dl[0] != "10" {
		t.Errorf("Expected [10] downloaded after reconcile, got %v", dl)
	}
}

func TestControlStep_I3_SIGKILL(t *testing.T) {
	mirror := setupMirror()
	defer mirror.Close()

	ws := t.TempDir()
	manifest := filepath.Join(ws, "manifest.txt")
	os.WriteFile(manifest, []byte("1\n2\n3\n4\n"), 0644)

	// Since we can't easily send SIGKILL to a child goroutine cleanly without
	// actually forking, we can test it by running the engine binary as a subprocess
	// and killing it. First, we need to build it.
	
	engineBin := filepath.Join(ws, "engine")
	cmdBuild := exec.Command("go", "build", "-o", engineBin, "./")
	if err := cmdBuild.Run(); err != nil {
		t.Skip("Failed to build engine binary for SIGKILL test", err)
	}
	
	cmd := exec.Command(engineBin, "--workspace", ws, "--datalake-layout", "hash", "--index-backend", "json", "control-step", "--iterations", "100", "--manifest", manifest, "--source-base", mirror.URL)
	cmd.Start()
	
	// Wait a tiny bit and kill
	time.Sleep(10 * time.Millisecond)
	cmd.Process.Kill()
	cmd.Wait()
	
	// Now run reconcile
	cmdReconcile := exec.Command(engineBin, "--workspace", ws, "--datalake-layout", "hash", "--index-backend", "json", "reconcile")
	if err := cmdReconcile.Run(); err != nil {
		t.Fatalf("Reconcile failed after SIGKILL: %v", err)
	}
	
	// Now resume
	cmdResume := exec.Command(engineBin, "--workspace", ws, "--datalake-layout", "hash", "--index-backend", "json", "control-step", "--iterations", "100", "--manifest", manifest, "--source-base", mirror.URL)
	if err := cmdResume.Run(); err != nil {
		t.Fatalf("Resume failed after SIGKILL: %v", err)
	}
	
	// Verify completion
	dl := lines(ws, "downloaded_books.txt")
	if len(dl) != 4 {
		t.Errorf("Expected 4 downloaded, got %v", dl)
	}
}
