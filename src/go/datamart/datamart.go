package datamart

import (
	"crypto/sha256"
	"encoding/hex"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type MetadataRecord struct {
	BookID      int     `json:"book_id"`
	Title       string  `json:"title"`
	Author      *string `json:"author"`
	Language    *string `json:"language"`
	ReleaseDate *string `json:"release_date"`
	HeaderPath  string  `json:"header_path"`
	BodyPath    string  `json:"body_path"`
	BodyBytes   int64   `json:"body_bytes"`
	SHA256      string  `json:"sha256"`
	IngestedAt  string  `json:"ingested_at"`
}

var (
	knownFields = map[string]string{
		"title":        "title",
		"author":       "author",
		"editor":       "editor",
		"illustrator":  "illustrator",
		"translator":   "translator",
		"release date": "release_date",
		"language":     "language",
		"credits":      "credits",
	}

	months = map[string]int{
		"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
		"july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
	}

	dateRe       = regexp.MustCompile(`^([a-zA-Z]+)\s+(\d{1,2}),\s+(\d{4})(?:\s+\[.*\])?$`)
	authorLifeRe = regexp.MustCompile(`,\s*\d{4}\s*[-–]\s*\d{4}\s*$`)
	wsRe         = regexp.MustCompile(`\s+`)
)

func normalizeWhitespace(s string) string {
	return strings.TrimSpace(wsRe.ReplaceAllString(s, " "))
}

func LoadLanguageMap(workspace string) map[string]string {
	mapping := make(map[string]string)
	path := filepath.Join(workspace, "spec", "language_map.txt")
	content, err := os.ReadFile(path)
	if err != nil {
		return mapping
	}
	
	lines := strings.Split(string(content), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			mapping[strings.ToLower(strings.TrimSpace(parts[0]))] = strings.TrimSpace(parts[1])
		}
	}
	return mapping
}

func parseReleaseDate(val string) *string {
	if val == "" {
		return nil
	}
	matches := dateRe.FindStringSubmatch(val)
	if matches == nil {
		return nil
	}
	
	monthStr := strings.ToLower(matches[1])
	monthNum, ok := months[monthStr]
	if !ok {
		return nil
	}
	
	day, _ := strconv.Atoi(matches[2])
	year, _ := strconv.Atoi(matches[3])
	
	t := time.Date(year, time.Month(monthNum), day, 0, 0, 0, 0, time.UTC)
	if t.Year() != year || int(t.Month()) != monthNum || t.Day() != day {
		return nil
	}
	
	res := t.Format("2006-01-02")
	return &res
}

func ParseHeader(header string, langMap map[string]string) map[string]string {
	fields := make(map[string]string)
	currentField := ""
	
	lines := strings.Split(header, "\n")
	for _, line := range lines {
		if (strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t")) && currentField != "" {
			if currentField == "release_date" {
				currentField = ""
				continue
			}
			cont := strings.TrimSpace(line)
			if cont != "" {
				fields[currentField] += " " + cont
			} else {
				currentField = ""
			}
			continue
		}
		
		currentField = ""
		parts := strings.SplitN(line, ":", 2)
		if len(parts) < 2 {
			continue
		}
		
		if strings.HasPrefix(parts[0], " ") || strings.HasPrefix(parts[0], "\t") {
			continue
		}
		
		name := normalizeWhitespace(parts[0])
		if mapped, ok := knownFields[strings.ToLower(name)]; ok {
			currentField = mapped
			val := normalizeWhitespace(parts[1])
			if val != "" {
				fields[currentField] = val
			}
		}
	}
	return fields
}

func ParseRecord(bookID int, header, body, headerPath, bodyPath, ingestedAt string, langMap map[string]string) MetadataRecord {
	rawFields := ParseHeader(header, langMap)
	
	record := MetadataRecord{
		BookID:     bookID,
		HeaderPath: filepath.ToSlash(headerPath),
		BodyPath:   filepath.ToSlash(bodyPath),
		BodyBytes:  int64(len(body)),
		IngestedAt: ingestedAt,
	}
	
	h := sha256.New()
	h.Write([]byte(body))
	record.SHA256 = hex.EncodeToString(h.Sum(nil))
	
	title, ok := rawFields["title"]
	if !ok || title == "" {
		log.Printf("WARNING: MISSING_TITLE for book %d", bookID)
		record.Title = "Unknown"
	} else {
		record.Title = title
	}
	
	if author, ok := rawFields["author"]; ok && author != "" {
		cleanAuthor := strings.TrimSpace(authorLifeRe.ReplaceAllString(author, ""))
		record.Author = &cleanAuthor
	}
	
	if lang, ok := rawFields["language"]; ok && lang != "" {
		lowerLang := strings.ToLower(lang)
		if code, mapped := langMap[lowerLang]; mapped {
			record.Language = &code
		} else {
			record.Language = &lowerLang
		}
	}
	
	if dateStr, ok := rawFields["release_date"]; ok {
		record.ReleaseDate = parseReleaseDate(dateStr)
	}
	
	return record
}

