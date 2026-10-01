package control

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestStateTracker_I1_NoDuplicates(t *testing.T) {
	ws := t.TempDir()
	tracker := NewStateTracker(ws)

	// Add same book multiple times
	tracker.MarkDownloaded(123)
	tracker.MarkDownloaded(123)
	tracker.MarkDownloaded(456)

	tracker.MarkIndexed([]int{123, 123, 456})

	dl := tracker.Downloaded()
	if !reflect.DeepEqual(dl, []int{123, 456}) {
		t.Errorf("Expected [123, 456], got %v", dl)
	}

	idx := tracker.Indexed()
	if !reflect.DeepEqual(idx, []int{123, 456}) {
		t.Errorf("Expected [123, 456], got %v", idx)
	}

	// Verify file content has no duplicates
	content, _ := os.ReadFile(filepath.Join(ws, "control", "downloaded_books.txt"))
	if string(content) != "123\n456\n" {
		t.Errorf("File has duplicates or wrong format: %q", string(content))
	}
}

func TestStateTracker_I4_Idempotency(t *testing.T) {
	ws := t.TempDir()
	tracker1 := NewStateTracker(ws)
	tracker1.MarkDownloaded(42)
	tracker1.MarkIndexed([]int{42})

	// Reload from disk
	tracker2 := NewStateTracker(ws)
	if !tracker2.IsDownloaded(42) {
		t.Error("Should be downloaded")
	}

	// Writing again should not modify the file
	statBefore, _ := os.Stat(filepath.Join(ws, "control", "downloaded_books.txt"))
	tracker2.MarkDownloaded(42)
	statAfter, _ := os.Stat(filepath.Join(ws, "control", "downloaded_books.txt"))

	if statBefore.ModTime() != statAfter.ModTime() || statBefore.Size() != statAfter.Size() {
		t.Error("File was modified but should be idempotent")
	}
}

func TestWorkspaceLock(t *testing.T) {
	ws := t.TempDir()
	lock1 := NewWorkspaceLock(ws)
	if err := lock1.Lock(); err != nil {
		t.Fatalf("Failed to acquire lock: %v", err)
	}

	lock2 := NewWorkspaceLock(ws)
	if err := lock2.Lock(); err == nil {
		t.Fatal("Second lock should fail")
	}

	if err := lock1.Unlock(); err != nil {
		t.Fatalf("Failed to release lock: %v", err)
	}

	if err := lock2.Lock(); err != nil {
		t.Fatalf("Should acquire after release: %v", err)
	}
	lock2.Unlock()
}

func TestRewrite_I2_NoLoss(t *testing.T) {
	ws := t.TempDir()
	tracker := NewStateTracker(ws)
	tracker.MarkDownloaded(1)
	tracker.MarkDownloaded(2)

	// Reconcile found only 1 in datalake
	tracker.Rewrite([]int{1, 3}, []int{1})

	dl := tracker.Downloaded()
	if !reflect.DeepEqual(dl, []int{1, 3}) {
		t.Errorf("Expected [1, 3], got %v", dl)
	}

	idx := tracker.Indexed()
	if !reflect.DeepEqual(idx, []int{1}) {
		t.Errorf("Expected [1], got %v", idx)
	}
}
