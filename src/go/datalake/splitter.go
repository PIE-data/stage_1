package datalake

import (
	"bytes"
	"errors"
	"regexp"
	"strings"
)

var ErrNoMarkers = errors.New("START or END	marker is missing.")

func Split(rawBytes []byte) (header string, body string, err error) {
	rawStr := string(bytes.ToValidUTF8(rawBytes, []byte("\uFFFD")))

	text := strings.ReplaceAll(rawStr, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")

	startMarker := "*** START OF THE PROJECT GUTENBERG EBOOK"
	endMarker := "*** END OF THE PROJECT GUTENBERG EBOOK"

	startIndex := -1
	for i, line := range lines {
		if strings.Contains(line, startMarker) {
			startIndex = i
			break
		}
	}

	endIndex := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.Contains(lines[i], endMarker) {
			endIndex = i
			break
		}
	}
	
	if startIndex == -1 || endIndex == -1 || endIndex <= startIndex {
		return "", "", ErrNoMarkers
	}

	headerLines := lines[:startIndex]
	bodyLines := lines[startIndex+1: endIndex]

	header = cleanHeader(strings.Join(headerLines, "\n"))
	body = cleanBody(strings.Join(bodyLines, "\n"))

	return header, body, nil
}

func cleanHeader(h string) string {
	h = strings.TrimPrefix(h , "\uFEFF")
	h = strings.TrimSpace(h)
	
	if h != "" {
		return h + "\n"
	}

	return "\n"
}

func cleanBody(b string) string {
	b = strings.TrimPrefix(b, "\uFEFF")

	var cleaned []string
	for _, line := range strings.Split(b, "\n") {
		cleaned = append(cleaned, strings.TrimRight(line, " \t"))
	}

	b = strings.Join(cleaned, "\n")

	re := regexp.MustCompile(`\n{3,}`)
	b = re.ReplaceAllString(b, "\n\n")

	b = strings.TrimSpace(b)

	if b != "" {
		return b + "\n"
	}
	return "\n"
}
