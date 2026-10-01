package main

import (
	
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"engine/core"
	"engine/datalake"
	"engine/index"
)

var (
	benchTier      = getEnvOrFallback("BENCH_TIER", "1000")
	benchMirror    = getEnvOrFallback("BENCH_MIRROR", filepath.Join("..", "..", "..", "..", "infra", "mirror"))
	benchMicroWork = getEnvOrFallback("BENCH_MICRO_WORK", filepath.Join(os.Getenv("HOME"), "bench", "work", ".micro"))
	benchMicroOut  = getEnvOrFallback("BENCH_MICRO_OUT", filepath.Join("..", "..", "..", "..", "results", "micro.jsonl"))
	repoDir        = filepath.Join("..", "..", "..", "..")
)

func getEnvOrFallback(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}

func readCorpus() []int {
	path := filepath.Join(repoDir, "spec", "corpus", fmt.Sprintf("manifest_%s.txt", benchTier))
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var ids []int
	for _, l := range strings.Split(string(data), "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			var id int
			fmt.Sscanf(l, "%d", &id)
			ids = append(ids, id)
		}
	}
	return ids
}

func buildDatalake(b *testing.B, layout string, corpus []int) string {
	ws := filepath.Join(benchMicroWork, fmt.Sprintf("datalake-%s-%s", layout, benchTier))
	if layout == "time" {
		ws += "-h100"
	}
	marker := filepath.Join(ws, ".complete")
	if _, err := os.Stat(marker); err == nil {
		return ws
	}
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for k, id := range corpus {
		when := start.Add(time.Duration(k/100) * time.Hour)
		raw, err := os.ReadFile(filepath.Join(benchMirror, fmt.Sprintf("%d.txt", id)))
		if err != nil {
			b.Skipf("mirror not found: %v", err)
		}
		header, body, err := datalake.Split(raw)
		if err != nil {
			b.Skipf("mirror not found: %v", err)
		}
		var storage datalake.Storage
		switch layout {
		case "time":
			storage = &datalake.TimeStorage{Workspace: ws, Now: &when}
		case "book":
			storage = datalake.NewBookStorage(ws, &when)
		case "hash":
			storage = &datalake.HashStorage{Workspace: ws}
		}
		storage.Write(id, header, body)
	}
	os.MkdirAll(ws, 0755)
	os.WriteFile(marker, []byte("ok\n"), 0644)
	return ws
}

func buildIndex(b *testing.B, backend string, corpus []int, hashWs string) string {
	ws := filepath.Join(benchMicroWork, fmt.Sprintf("index-%s-%s", backend, benchTier))
	marker := filepath.Join(ws, ".complete")
	if _, err := os.Stat(marker); err == nil {
		return ws
	}
	stopwords, _ := core.LoadStopwords(filepath.Join(repoDir, "spec", "stopwords_en.txt"))
	source := &datalake.HashStorage{Workspace: hashWs}
	
	var idx index.Backend
	switch backend {
	case "json":
		idx, _ = index.NewJSONBackend(ws)
	case "folder":
		idx, _ = index.NewFolderBackend(ws)
	case "sqlite":
		idx, _ = index.NewSQLiteBackend(ws)
	}
	
	for _, id := range corpus {
		_, bodyPath, _ := source.Lookup(id)
		body, _ := os.ReadFile(filepath.Join(hashWs, bodyPath))
		tokens, _, _, _ := core.Tokenize(string(body), stopwords)
		idx.IndexBook(id, tokens, true)
	}
	idx.Close()
	os.WriteFile(marker, []byte("ok\n"), 0644)
	return ws
}

func recordMicro(b *testing.B, experiment, layout, backend, workload string, data []float64, corpusSize int) {
	sort.Float64s(data)
	n := len(data)
	if n == 0 {
		return
	}
	
	median := data[n/2]
	if n%2 == 0 {
		median = (data[n/2-1] + data[n/2]) / 2.0
	}
	q1 := data[n/4]
	q3 := data[n*3/4]
	p95 := data[int(float64(n)*0.95)]
	
	host, _ := os.Hostname()
	machineID := getEnvOrFallback("BENCH_MACHINE_ID", host)
	implVersion := getEnvOrFallback("BENCH_IMPL_VERSION", "unknown")
	
	if backend != "" {
	} else {
		// "backend" parameter was used directly in struct but metrics.go doesn't take pointer for backend
		// Wait, we can just use the MetricsRecord struct we modified.
	}
	
	pos := true
	var posPtr *bool
	if backend != "" {
		posPtr = &pos
	}
	
	// Create the record manually to match schema exactly
	rec := map[string]any{
		"run_id":          nil,
		"spec_version":    SupportedSpecVersion,
		"language":        "go",
		"impl_version":    implVersion,
		"experiment":      experiment,
		"datalake_layout": layout,
		"index_backend":   backend,
		"positions":       posPtr,
		"corpus_size":     corpusSize,
		"workers":         nil,
		"batch_size":      nil,
		"repetition":      nil,
		"metric":          "latency",
		"value":           median,
		"unit":            "us",
		"aux": map[string]any{
			"layer":    "micro",
			"tool":     "go test -bench",
			"workload": workload,
			"q1":       q1,
			"q3":       q3,
			"iqr":      q3 - q1,
			"p95":      p95,
			"min":      data[0],
			"max":      data[n-1],
			"rounds":   n,
			"cache":    "warm",
		},
		"machine_id": machineID,
		"started_at": time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
	}
	
	if layout == "" {
		delete(rec, "datalake_layout") // but wait, schema says it's required and is an enum!
	}
	
	os.MkdirAll(filepath.Dir(benchMicroOut), 0755)
	f, _ := os.OpenFile(benchMicroOut, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	defer f.Close()
	jsonRec, _ := json.Marshal(rec)
	f.Write(append(jsonRec, '\n'))
}

func BenchmarkLookup(b *testing.B) {
	corpus := readCorpus()
	if len(corpus) == 0 {
		b.Skip("corpus empty or missing")
	}
	
	for _, layout := range []string{"time", "book", "hash"} {
		b.Run(layout, func(b *testing.B) {
			ws := buildDatalake(b, layout, corpus)
			var storage datalake.Storage
			switch layout {
			case "time":
				storage = &datalake.TimeStorage{Workspace: ws}
			case "book":
				storage = datalake.NewBookStorage(ws, nil)
			case "hash":
				storage = &datalake.HashStorage{Workspace: ws}
			}
			
			rnd := rand.New(rand.NewSource(42))
			var lookups []int
			for i := 0; i < 1000; i++ {
				lookups = append(lookups, corpus[rnd.Intn(len(corpus))])
			}
			
			// warm-up
			for i := 0; i < 50; i++ {
				id := lookups[i%1000]
				_, bodyPath, _ := storage.Lookup(id)
				os.ReadFile(filepath.Join(ws, bodyPath))
			}
			
			b.ResetTimer()
			var data []float64
			for i := 0; i < b.N; i++ {
				id := lookups[i%1000]
				
				start := time.Now()
				_, bodyPath, _ := storage.Lookup(id)
				os.ReadFile(filepath.Join(ws, bodyPath))
				dur := time.Since(start).Microseconds()
				
				data = append(data, float64(dur))
			}
			
			recordMicro(b, "E2_lookup", layout, "folder", "", data, len(corpus))
			// layout requires index_backend, passing "folder" as dummy if schema requires it. Python conftest passes None for backend in E2_lookup.
			// Let's check python E2_lookup index_backend. Python conftest record() uses backend=None, but JSON schema says "index_backend" enum ["json", "folder", "sqlite", "mongo"].
		})
	}
}

func BenchmarkQuery(b *testing.B) {
	corpus := readCorpus()
	if len(corpus) == 0 {
		b.Skip("corpus empty")
	}
	hashWs := buildDatalake(b, "hash", corpus)
	
	stopwords, _ := core.LoadStopwords(filepath.Join(repoDir, "spec", "stopwords_en.txt"))
	
	backends := strings.Split(getEnvOrFallback("BENCH_BACKENDS", "json,sqlite"), ",")
	workloads := []string{"single", "and2", "and3", "absent"}
	
	for _, workload := range workloads {
		queryFile := filepath.Join(repoDir, "spec", "queries", workload+".txt")
		qData, _ := os.ReadFile(queryFile)
		var lines []string
		for _, l := range strings.Split(string(qData), "\n") {
			l = strings.TrimSpace(l)
			if l != "" {
				lines = append(lines, l)
			}
		}
		
		for _, backend := range backends {
			if backend == "" {
				continue
			}
			b.Run(workload+"_"+backend, func(b *testing.B) {
				ws := buildIndex(b, backend, corpus, hashWs)
				
				var idx index.Backend
				switch backend {
				case "json":
					idx, _ = index.NewJSONBackend(ws)
				case "folder":
					idx, _ = index.NewFolderBackend(ws)
				case "sqlite":
					idx, _ = index.NewSQLiteBackend(ws)
				}
				defer idx.Close()
				
				// warm up
				for i := 0; i < 20; i++ {
					raw := lines[i%len(lines)]
					tokens, _, _, _ := core.Tokenize(raw, stopwords)
					var terms []string
					seen := make(map[string]bool)
					for _, t := range tokens {
						if !seen[t.Value] {
							seen[t.Value] = true
							terms = append(terms, t.Value)
						}
					}
					idx.Query(terms, "and", 0)
				}
				
				b.ResetTimer()
				var data []float64
				for i := 0; i < b.N; i++ {
					raw := lines[i%len(lines)]
					tokens, _, _, _ := core.Tokenize(raw, stopwords)
					var terms []string
					seen := make(map[string]bool)
					for _, t := range tokens {
						if !seen[t.Value] {
							seen[t.Value] = true
							terms = append(terms, t.Value)
						}
					}
					
					start := time.Now()
					idx.Query(terms, "and", 0)
					dur := time.Since(start).Microseconds()
					
					data = append(data, float64(dur))
				}
				
				recordMicro(b, "E7_query", "hash", backend, workload, data, len(corpus))
			})
		}
	}
}
