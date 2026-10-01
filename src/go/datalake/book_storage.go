package datalake

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"engine/datamart"
)

type BookStorage struct {
	Workspace string
	Now       *time.Time
	langMap   map[string]string
}

func NewBookStorage(workspace string, now *time.Time) *BookStorage {
	return &BookStorage{
		Workspace: workspace,
		Now:       now,
		langMap:   datamart.LoadLanguageMap(workspace),
	}
}

func (s *BookStorage) Write(bookID int, header, body string) (string, string, error) {
	idStr := strconv.Itoa(bookID)
	root := filepath.Join(s.Workspace, "datalake", "books", idStr)
	if err := os.MkdirAll(root, 0755); err != nil {
		return "", "", err
	}

	headerPath := filepath.Join(root, "header.txt")
	bodyPath := filepath.Join(root, "body.txt")
	metaPath := filepath.Join(root, "meta.json")

	if err := WriteAtomically(headerPath, []byte(header)); err != nil {
		return "", "", err
	}
	if err := WriteAtomically(bodyPath, []byte(body)); err != nil {
		return "", "", err
	}

	relHeader := filepath.ToSlash(filepath.Join("datalake", "books", idStr, "header.txt"))
	relBody := filepath.ToSlash(filepath.Join("datalake", "books", idStr, "body.txt"))
	var ingestedAt string
	if s.Now != nil {
		ingestedAt = s.Now.UTC().Format("2006-01-02T15:04:05Z")
	} else {
		ingestedAt = time.Now().UTC().Format("2006-01-02T15:04:05Z")
	}

	record := datamart.ParseRecord(bookID, header, body, relHeader, relBody, ingestedAt, s.langMap)
	
	buffer := &bytes.Buffer{}
	encoder := json.NewEncoder(buffer)
	encoder.SetEscapeHTML(false)
	err := encoder.Encode(record)
	metaBytes := buffer.Bytes()
	if err != nil {
		return "", "", err
	}
	if err := WriteAtomically(metaPath, metaBytes); err != nil {
		return "", "", err
	}

	return relHeader, relBody, nil
}

func (s *BookStorage) Lookup(bookID int) (string, string, error) {
	idStr := strconv.Itoa(bookID)
	root := filepath.Join(s.Workspace, "datalake", "books", idStr)
	
	headerPath := filepath.Join(root, "header.txt")
	bodyPath := filepath.Join(root, "body.txt")
	
	if _, err := os.Stat(headerPath); err != nil {
		return "", "", ErrNotFound
	}
	if _, err := os.Stat(bodyPath); err != nil {
		return "", "", ErrNotFound
	}
	
	relHeader := filepath.ToSlash(filepath.Join("datalake", "books", idStr, "header.txt"))
	relBody := filepath.ToSlash(filepath.Join("datalake", "books", idStr, "body.txt"))
	return relHeader, relBody, nil
}

func (s *BookStorage) ListNew(since time.Time) ([]int, error) {
	root := filepath.Join(s.Workspace, "datalake", "books")
	
	entries, err := os.ReadDir(root)
	if os.IsNotExist(err) {
		return nil, nil
	} else if err != nil {
		return nil, err
	}
	
	var newBooks []int
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		
		bodyPath := filepath.Join(root, entry.Name(), "body.txt")
		info, err := os.Stat(bodyPath)
		if err != nil {
			continue
		}
		
		if info.ModTime().Unix() >= since.Unix() {
			id, err := strconv.Atoi(entry.Name())
			if err == nil {
				newBooks = append(newBooks, id)
			}
		}
	}
	
	return newBooks, nil
}
