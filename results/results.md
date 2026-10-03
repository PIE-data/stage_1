# Benchmark results (Andrea's laptop)

Machine: MSI laptop, Intel i7-1255U (2 performance + 8 efficiency cores, 15 W),
16 GB RAM (10 GiB given to WSL2), NVMe SSD; Ubuntu on WSL2, ext4 under `~/bench`.
Python 3.14, Node 22, Go 1.26. Every download comes from a local mirror
(`tools/mirror_server.py`), never from the live site.

All folders below were measured on the code that is on `main`; changes merged
after a folder was measured do not touch the measured code paths. Use ONLY the
rows listed here: anything else is obsolete.

Each folder has `raw.jsonl` (one SPEC §8 record per repetition) and
`summary.csv` (median, IQR, min, max per configuration; `t-meta` adds a last
column `batch_size`). Report medians + IQR. For short commands (E3) use the
`wall_time_internal` rows: the outer time is mostly interpreter start-up.

## Datalake (E1-E5, 1000 books)

| Experiment | Python, Node | Go |
|---|---|---|
| E1 download + write, 1 and 8 workers | `t1000-datalake` | `t1000-go` |
| E3 detection of new books (`scan-new --since`) | `t1000-py-node`, E3 rows only | `t1000-go` |
| E4 recovery after SIGKILL | `t1000-datalake` | `t1000-go` |
| E5 storage per layout | `t1000-datalake` | same bytes (E5 rows in `t1000-go` are identical) |
| E2 lookup, E3 scan (micro) | `micro.jsonl`, `micro-pytest.json` (Python only) | - |

## Inverted index (E6 build, E8 +50 books, E7 query)

| Experiment | Python | Node | Go |
|---|---|---|---|
| E6 json, 1000 books | `t1000-py-node-e6e8` | `t1000-py-node-e6e8` | `t1000-go-e6e8` |
| E6 sqlite, 1000 books | `t1000-py-sqlite` | `t1000-py-node-e6e8` | `t1000-go-e6e8` |
| E8 json, 1000 books | `t1000-py-node-e8` | `t1000-py-node-e8` | `t1000-go-e6e8` |
| E8 sqlite, 1000 books | `t1000-py-sqlite` | `t1000-py-node-e8` | `t1000-go-e6e8` |
| E6/E8 json + sqlite, 100 books | `t100` | `t100` | - |
| E6/E8 folder, 100 books | `t100-folder` | `t100-folder` | `t100-folder-go` |
| E7 query (micro) | `micro.jsonl` | - | - |

Ignore: Python sqlite rows in `t1000-py-node-e6e8` and `t1000-py-node-e8`, and
E6 rows in `t1000-py-node` (older runs, superseded by `t1000-py-sqlite` and
`t1000-py-node-e6e8`).

The folder backend is measured at 100 books only: it writes one file per term
with an fsync each, so 1000 books take hours. Declared as a limit in the report.

## Metadata datamart (E12 build, E13 queries)

| Experiment | Folder |
|---|---|
| E12 `metadata --all`: 3 languages x 3 layouts x batch size 1 / 500, 100 and 1000 books | `t-meta` (summary.csv, raw.jsonl) and `t-meta.log` |
| E13 queries Q1-Q4 of SPEC §5.4 + Q3_range, 100 and 1000 books (micro, Python) | `t-meta/micro-metadata.jsonl` (each record carries the SQLite query plan) |

E12 also checks that the three languages build identical rows: see the lines
`identical metadata rows in go, node, python` in `t-meta.log` (all layouts,
both tiers).

## Main findings (medians)

- E6, 1000 books: json Python 109.5 s / Node 78.8 s / Go 29.4 s; sqlite
  118.9 / 79.4 / 97.5 s. Peak memory json 914 MB / 1.6 GB / 2.25 GB, sqlite
  450 MB / 760 MB / 1.42 GB.
- E8 (+50 books on 1000): json 26.4 / 23.0 / 8.6 s; sqlite 26.3 / 12.9 / 19.3 s.
- Folder, 100 books: E6 584.8 / 3084.9 / 522.1 s; E8 672.3 / 2108.5 / 618.6 s.
- E1, 1000 books, 1 -> 8 workers: Python ~44 -> ~15 s, Go ~42 -> ~9.6 s,
  Node ~70 s with no gain (synchronous file writes on its event loop).
- E4: no book lost or duplicated in any language or layout.
- E5: allocated space time 390.9 / hash 393.1 / book 399.0 MB (< 2 % apart).
- E12, 1000 books: 1.5-2.0 s in every language; batch size 1 vs 500 only ~10 %
  apart (WAL + synchronous=NORMAL: a commit does not fsync). Most of the time is
  reading the bodies and computing their SHA-256, not inserting rows.
- E13, 100 -> 1000 books: indexed queries stay at 2-4 us; Q4 (GROUP BY)
  5.9 -> 32 us; Q3 (`LIKE prefix%`) 6.2 -> 49 us because it scans the table
  (case-insensitive LIKE cannot use the BINARY title index). Q3_range (4 us)
  shows what the index would give; it is case-sensitive, so it is not a
  drop-in replacement for Q3.

Not measured: sqlite at 10 000 books (E10/E11 scaling) -- declared as a limit.
