package datalake

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type HashStorage struct {
	Workspace string
}

func (s *HashStorage) getTargetDir(bookID int) string {
	id6 := fmt.Sprintf("%06d", bookID)
	return filepath.Join(s.Workspace, "datalake", id6[0:2], id6[2:4])
}

func (s *HashStorage) getRelTargetDir(bookID int) string {
	id6 := fmt.Sprintf("%06d", bookID)
	return filepath.Join("datalake", id6[0:2], id6[2:4])
}

func (s *HashStorage) Write(bookID int, header, body string) (string, string, error) {
	targetDir := s.getTargetDir(bookID)

	headerPath := filepath.Join(targetDir, fmt.Sprintf("%d.header.txt", bookID))
	bodyPath := filepath.Join(targetDir, fmt.Sprintf("%d.body.txt", bookID))

	if err := WriteAtomically(headerPath, []byte(header)); err != nil {
		return "", "", err
	}
	if err := WriteAtomically(bodyPath, []byte(body)); err != nil {
		return "", "", err
	}

	relDir := s.getRelTargetDir(bookID)
	relHeader := filepath.ToSlash(filepath.Join(relDir, fmt.Sprintf("%d.header.txt", bookID)))
	relBody := filepath.ToSlash(filepath.Join(relDir, fmt.Sprintf("%d.body.txt", bookID)))

	return relHeader, relBody, nil
}

func (s *HashStorage) Lookup(bookID int) (string, string, error) {
	targetDir := s.getTargetDir(bookID)

	headerPath := filepath.Join(targetDir, fmt.Sprintf("%d.header.txt", bookID))
	bodyPath := filepath.Join(targetDir, fmt.Sprintf("%d.body.txt", bookID))

	if _, err := os.Stat(headerPath); err == nil {
		if _, err := os.Stat(bodyPath); err == nil {
			relDir := s.getRelTargetDir(bookID)
			relHeader := filepath.ToSlash(filepath.Join(relDir, fmt.Sprintf("%d.header.txt", bookID)))
			relBody := filepath.ToSlash(filepath.Join(relDir, fmt.Sprintf("%d.body.txt", bookID)))
			return relHeader, relBody, nil
		}
	}
	return "", "", ErrNotFound
}

func (s *HashStorage) ListNew(since time.Time) ([]int, error) {
	root := filepath.Join(s.Workspace, "datalake")
	var newBooks []int

	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if !d.IsDir() && strings.HasSuffix(d.Name(), ".body.txt") {
			info, err := d.Info()
			// E3 check: whole tree walked, compare body modification time
			if err == nil && info.ModTime().Unix() >= since.Unix() {
				idStr := strings.TrimSuffix(d.Name(), ".body.txt")
				if id, err := strconv.Atoi(idStr); err == nil {
					newBooks = append(newBooks, id)
				}
			}
		}
		return nil
	})
	
	if os.IsNotExist(err) {
		return nil, nil
	}
	return newBooks, err
}
