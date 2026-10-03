package datalake

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ErrHTTPNotFound marks a 404: the book does not exist, never retried.
var ErrHTTPNotFound = errors.New("HTTP 404")

const DefaultSourceBase = "https://www.gutenberg.org"

func Download(ctx context.Context, bookID int, sourceBase string) ([]byte, error) {
	if sourceBase == "" {
		sourceBase = DefaultSourceBase
	}
	url := fmt.Sprintf("%s/cache/epub/%d/pg%d.txt", strings.TrimRight(sourceBase, "/"), bookID, bookID)
	client := &http.Client{Timeout: 60 * time.Second}
	backoffs := []time.Duration{1 * time.Second, 2 * time.Second, 4 * time.Second}
	var lastErr error
	for attempt := 0; attempt <= 3; attempt++ {
		if attempt > 0 {
			select {
			case <-time.After(backoffs[attempt-1]):
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("User-Agent", "ULPGC-BigData-Stage1/PIE-data (+https://github.com/PIE-data/stage_1)")
		resp, err := client.Do(req)
		if err != nil {
			lastErr = err
			continue // Network error -> retry
		}
		if resp.StatusCode == 404 {
			resp.Body.Close()
			return nil, fmt.Errorf("book %d: %w", bookID, ErrHTTPNotFound) // never retry a 404
		}
		if resp.StatusCode >= 500 && resp.StatusCode < 600 {
			resp.Body.Close()
			lastErr = fmt.Errorf("server error %d", resp.StatusCode)
			continue // 5xx -> retry
		}
		if resp.StatusCode != 200 {
			resp.Body.Close()
			return nil, fmt.Errorf("unexpected status %d", resp.StatusCode)
		}
		data, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			lastErr = err
			continue // Read error -> retry
		}
		return data, nil
	}
	return nil, fmt.Errorf("failed after 3 retries, last error: %w", lastErr)
}
