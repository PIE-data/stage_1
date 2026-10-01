package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

type MetricsRecord struct {
	RunID          *string        `json:"run_id"`
	SpecVersion    string         `json:"spec_version"`
	Language       string         `json:"language"`
	ImplVersion    *string        `json:"impl_version"`
	Experiment     string         `json:"experiment"`
	DatalakeLayout string         `json:"datalake_layout"`
	IndexBackend   string         `json:"index_backend"`
	Positions      *bool          `json:"positions"`
	CorpusSize     *int           `json:"corpus_size"`
	Workers        *int           `json:"workers"`
	BatchSize      *int           `json:"batch_size"`
	Repetition     *int           `json:"repetition"`
	Metric         string         `json:"metric"`
	Value          float64        `json:"value"`
	Unit           string         `json:"unit"`
	Aux            map[string]any `json:"aux"`
	MachineID      string         `json:"machine_id"`
	StartedAt      string         `json:"started_at"`
}

func writeMetrics(metricsOut, startedAt string, wallTimeMs float64, command, datalakeLayout, indexBackend string) {
	experiment := getEnvOrNil("BENCH_EXPERIMENT")
	if experiment == nil {
		s := "cli_" + command
		experiment = &s
	}
	machineID := getEnvOrNil("BENCH_MACHINE_ID")
	if machineID == nil {
		if host, err := os.Hostname(); err == nil {
			machineID = &host
		} else {
			s := "unknown"
			machineID = &s
		}
	}

	record := MetricsRecord{
		RunID:          getEnvOrNil("BENCH_RUN_ID"),
		SpecVersion:    SupportedSpecVersion,
		Language:       "go",
		ImplVersion:    getEnvOrNil("BENCH_IMPL_VERSION"),
		Experiment:     *experiment,
		DatalakeLayout: datalakeLayout,
		IndexBackend:   indexBackend,
		Positions:      nil,
		CorpusSize:     getEnvIntOrNil("BENCH_CORPUS_SIZE"),
		Workers:        nil,
		BatchSize:      nil,
		Repetition:     getEnvIntOrNil("BENCH_REPETITION"),
		Metric:         "wall_time",
		Value:          wallTimeMs,
		Unit:           "ms",
		Aux:            make(map[string]any),
		MachineID:      *machineID,
		StartedAt:      startedAt,
	}

	buf := &bytes.Buffer{}
	enc := json.NewEncoder(buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(record); err == nil {
		f, err := os.OpenFile(metricsOut, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err == nil {
			f.Write([]byte(strings.TrimSpace(buf.String()) + "\n"))
			f.Close()
		} else {
			fmt.Fprintf(os.Stderr, "failed to open metrics file: %v\n", err)
		}
	}
}
