package control

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"engine/datalake"
)

func UTCNowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05Z")
}

type StateTracker struct {
	dir        string
	mu         sync.Mutex
	downloaded map[int]bool
	indexed    map[int]bool
	failed     map[int]bool
}

func NewStateTracker(workspace string) *StateTracker {
	dir := filepath.Join(workspace, "control")
	return &StateTracker{
		dir:        dir,
		downloaded: readIDs(filepath.Join(dir, "downloaded_books.txt")),
		indexed:    readIDs(filepath.Join(dir, "indexed_books.txt")),
		failed:     readIDs(filepath.Join(dir, "failed_books.txt")),
	}
}

func readIDs(path string) map[int]bool {
	ids := make(map[int]bool)
	f, err := os.Open(path)
	if err != nil {
		return ids
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		parts := strings.SplitN(line, "\t", 2)
		idStr := strings.TrimSpace(parts[0])
		if id, err := strconv.Atoi(idStr); err == nil {
			ids[id] = true
		}
	}
	return ids
}

func (s *StateTracker) IsDownloaded(bookID int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.downloaded[bookID]
}

func (s *StateTracker) IsIndexed(bookID int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.indexed[bookID]
}

func (s *StateTracker) IsFailed(bookID int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.failed[bookID]
}

func (s *StateTracker) Downloaded() []int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return sortKeys(s.downloaded)
}

func (s *StateTracker) Indexed() []int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return sortKeys(s.indexed)
}

func (s *StateTracker) ReadyToIndex() []int {
	s.mu.Lock()
	defer s.mu.Unlock()
	var ids []int
	for id := range s.downloaded {
		if !s.indexed[id] {
			ids = append(ids, id)
		}
	}
	sort.Ints(ids)
	return ids
}

func sortKeys(m map[int]bool) []int {
	var keys []int
	for k := range m {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	return keys
}

func (s *StateTracker) appendFile(name string, lines []string) error {
	if err := os.MkdirAll(s.dir, 0755); err != nil {
		return err
	}
	var buf bytes.Buffer
	for _, line := range lines {
		buf.WriteString(line + "\n")
	}

	f, err := os.OpenFile(filepath.Join(s.dir, name), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	if _, err := f.Write(buf.Bytes()); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	return nil
}

func (s *StateTracker) MarkDownloaded(bookID int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.downloaded[bookID] {
		return nil
	}
	if err := s.appendFile("downloaded_books.txt", []string{strconv.Itoa(bookID)}); err != nil {
		return err
	}
	s.downloaded[bookID] = true
	return nil
}

func (s *StateTracker) MarkIndexed(bookIDs []int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	var newIDs []string
	for _, id := range bookIDs {
		if !s.indexed[id] {
			newIDs = append(newIDs, strconv.Itoa(id))
			s.indexed[id] = true
		}
	}
	if len(newIDs) == 0 {
		return nil
	}
	return s.appendFile("indexed_books.txt", newIDs)
}

func (s *StateTracker) MarkFailed(bookID int, reason string, when string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if when == "" {
		when = UTCNowISO()
	}
	line := fmt.Sprintf("%d\t%s\t%s", bookID, reason, when)
	if err := s.appendFile("failed_books.txt", []string{line}); err != nil {
		return err
	}
	s.failed[bookID] = true
	return nil
}

func (s *StateTracker) Rewrite(downloaded []int, indexed []int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if err := os.MkdirAll(s.dir, 0755); err != nil {
		return err
	}

	write := func(name string, ids []int) error {
		var buf strings.Builder
		for _, id := range ids {
			buf.WriteString(strconv.Itoa(id) + "\n")
		}
		return datalake.WriteAtomically(filepath.Join(s.dir, name), []byte(buf.String()))
	}

	if err := write("downloaded_books.txt", downloaded); err != nil {
		return err
	}
	if err := write("indexed_books.txt", indexed); err != nil {
		return err
	}

	s.downloaded = make(map[int]bool)
	for _, id := range downloaded {
		s.downloaded[id] = true
	}
	s.indexed = make(map[int]bool)
	for _, id := range indexed {
		s.indexed[id] = true
	}
	return nil
}

type WorkspaceLock struct {
	path string
	f    *os.File
}

func NewWorkspaceLock(workspace string) *WorkspaceLock {
	return &WorkspaceLock{
		path: filepath.Join(workspace, "control", "run.lock"),
	}
}

func (w *WorkspaceLock) Lock() error {
	if err := os.MkdirAll(filepath.Dir(w.path), 0755); err != nil {
		return err
	}
	f, err := os.OpenFile(w.path, os.O_CREATE|os.O_RDWR, 0666)
	if err != nil {
		return err
	}

	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		return fmt.Errorf("%s is held by another process: %v", w.path, err)
	}

	w.f = f
	return nil
}

func (w *WorkspaceLock) Unlock() error {
	if w.f == nil {
		return nil
	}
	err := syscall.Flock(int(w.f.Fd()), syscall.LOCK_UN)
	w.f.Close()
	w.f = nil
	return err
}
