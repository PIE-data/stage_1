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

type TimeStorage struct {
	Workspace string
	Now       *time.Time
}

func (s *TimeStorage) current() time.Time {
	if s.Now != nil {
		return *s.Now
	}
	return time.Now().UTC()
}

func (s *TimeStorage) Write(bookID int, header, body string) (string, string, error) {
	t := s.current()
	dateStr := t.Format("20060102")
	hourStr := t.Format("15")
	targetDir := filepath.Join(s.Workspace, "datalake", dateStr, hourStr)
	headerPath := filepath.Join(targetDir, fmt.Sprintf("%d.header.txt", bookID))
	bodyPath := filepath.Join(targetDir, fmt.Sprintf("%d.body.txt", bookID))
	if err := WriteAtomically(headerPath, []byte(header)); err != nil {
		return "", "", err
	}
	if err := WriteAtomically(bodyPath, []byte(body)); err != nil {
		return "", "", err
	}
	relHeader := filepath.ToSlash(filepath.Join("datalake", dateStr, hourStr, fmt.Sprintf("%d.header.txt", bookID)))
	relBody := filepath.ToSlash(filepath.Join("datalake", dateStr, hourStr, fmt.Sprintf("%d.body.txt", bookID)))
	return relHeader, relBody, nil
}

func (s *TimeStorage) Lookup(bookID int) (string, string, error) {
	root := filepath.Join(s.Workspace, "datalake")
	targetBody := fmt.Sprintf("%d.body.txt", bookID)
	var foundHeader, foundBody string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if !d.IsDir() && d.Name() == targetBody {
			bodyPath := path
			headerPath := filepath.Join(filepath.Dir(path), fmt.Sprintf("%d.header.txt", bookID))
			
			if _, err := os.Stat(headerPath); err == nil {
				relBody, _ := filepath.Rel(s.Workspace, bodyPath)
				relHeader, _ := filepath.Rel(s.Workspace, headerPath)
				foundBody = filepath.ToSlash(relBody)
				foundHeader = filepath.ToSlash(relHeader)
				return filepath.SkipAll // Stop searching, we found it!
			}
		}
		return nil
	})
	if err == nil && foundBody == "" {
		return "", "", ErrNotFound
	}
	return foundHeader, foundBody, err
}

func (s *TimeStorage) ListNew(since time.Time) ([]int, error) {
	root := filepath.Join(s.Workspace, "datalake")
	sinceDate := since.Format("20060102")
	sinceHour := since.Format("15")
	var newBooks []int
	dates, err := os.ReadDir(root)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	for _, date := range dates {
		if date.IsDir() && date.Name() >= sinceDate {
			datePath := filepath.Join(root, date.Name())
			hours, _ := os.ReadDir(datePath)
			for _, hour := range hours {
				// If date is completely after sinceDate, we want all hours. Otherwise, check hour.
				if hour.IsDir() && (date.Name() > sinceDate || hour.Name() >= sinceHour) {
					hourPath := filepath.Join(datePath, hour.Name())
					files, _ := os.ReadDir(hourPath)
					for _, file := range files {
						if strings.HasSuffix(file.Name(), ".body.txt") {
							idStr := strings.TrimSuffix(file.Name(), ".body.txt")
							if id, err := strconv.Atoi(idStr); err == nil {
								newBooks = append(newBooks, id)
							}
						}
					}
				}
			}
		}
	}
	return newBooks, nil
}
