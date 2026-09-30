package datalake

import (
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type BookStorage struct {
	Workspace string
}

func (s *BookStorage) Write(bookID int, header, body string) (string, string, error) {
	bookDir := filepath.Join(s.Workspace, "datalake", "books", strconv.Itoa(bookID))

	headerPath := filepath.Join(bookDir, "header.txt")
	bodyPath := filepath.Join(bookDir, "body.txt")

	if err := WriteAtomically(headerPath, []byte(header)); err != nil {
		return "", "", err
	}
	if err := WriteAtomically(bodyPath, []byte(body)); err != nil {
		return "", "", err
	}

	relHeader := filepath.ToSlash(filepath.Join("datalake", "books", strconv.Itoa(bookID), "header.txt"))
	relBody := filepath.ToSlash(filepath.Join("datalake", "books", strconv.Itoa(bookID), "body.txt"))

	return relHeader, relBody, nil
}

func (s *BookStorage) Lookup(bookID int) (string, string, error) {
	bookDir := filepath.Join(s.Workspace, "datalake", "books", strconv.Itoa(bookID))

	headerPath := filepath.Join(bookDir, "header.txt")
	bodyPath := filepath.Join(bookDir, "body.txt")

	if _, err := os.Stat(headerPath); err == nil {
		if _, err := os.Stat(bodyPath); err == nil {
			relHeader := filepath.ToSlash(filepath.Join("datalake", "books", strconv.Itoa(bookID), "header.txt"))
			relBody := filepath.ToSlash(filepath.Join("datalake", "books", strconv.Itoa(bookID), "body.txt"))
			return relHeader, relBody, nil
		}
	}
	return "", "", ErrNotFound
}

func (s *BookStorage) ListNew(since time.Time) ([]int, error) {
	root := filepath.Join(s.Workspace, "datalake", "books")
	var newBooks []int

	entries, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	for _, entry := range entries {
		if entry.IsDir() {
			bodyPath := filepath.Join(root, entry.Name(), "body.txt")
			if info, err := os.Stat(bodyPath); err == nil {
				// E3 check: whole tree walked, compare body modification time
				if info.ModTime().Unix() >= since.Unix() {
					if id, err := strconv.Atoi(entry.Name()); err == nil {
						newBooks = append(newBooks, id)
					}
				}
			}
		}
	}
	return newBooks, nil
}
