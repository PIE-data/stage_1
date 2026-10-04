# Stage 1 — Building the Data Layer

## One-command offline demo

After installing the prerequisites and Node dependencies below, run from the repository root:

```sh
python tools/run_sample.py --lang python
# OR
python tools/run_sample.py --lang node
# OR (builds the Go CLI first)
python tools/run_sample.py --lang go
```

The demo uses three committed Gutenberg books from `data/sample/`.
It requires no network access and starts no mirror server.

It creates a fresh temporary workspace and runs:

1. Version verification.
2. Offline splitting into the datalake.
3. Metadata generation.
4. Indexing with positions.
5. Filesystem lookup.
6. An AND query.
7. Detection of books awaiting indexing.
8. Canonical index export.

Expected results include:

- Three metadata records and three indexed books.
- Query `says` returns book `8527`.
- `scan-new` prints nothing after indexing.
- A final `SUCCESS` message and the workspace location.

The workspace is retained for inspection. The default layout is `hash` and
the default index backend is `sqlite`.

To choose a new output directory or another storage configuration:

```sh
python tools/run_sample.py --workspace sample-workspace --layout book --backend json
```

The specified workspace must not already exist. Available layouts are
`time`, `book`, and `hash`; available index backends are `json`, `folder`,
and `sqlite`.

This three-book sample is not the 20-book conformance corpus. Its hash must
not be compared with `spec/golden/expected.sha256`.

## Specification and implementation status

[docs/SPEC.md](docs/SPEC.md) is the normative contract.
The required specification version is stored in `spec/SPEC_VERSION`.
A CLI refuses to run if its supported version differs.

Python (reference), Node.js and Go implement the same command-line interface
(SPEC §1): `download`, `split`, `metadata`, `index`, `query`, `lookup`,
`scan-new`, `control-step`, `reconcile`, `export-canonical`. All three pass the
conformance gate in every backend and layout (27/27 cells, tag
`conformance-green`). The offline demo can run any of the three; it uses
Python's standard library as a portable command runner.

The Node metadata receipt rules and their current limitations are documented
in [docs/NODE_INGESTION_PROPOSAL.md](docs/NODE_INGESTION_PROPOSAL.md).

## Prerequisites and installation

Run installation commands from the repository root.

| Component | Requirement |
|---|---|
| Python | 3.11 or newer |
| Node.js | Node 22 LTS, using an up-to-date 22.x release, or a newer supported release |
| Go | 1.22 or newer, for the Go implementation |
| Git | Required to clone the repository |
| Bash and sha256sum | Required by `tools/run_conformance.sh` |

Dependency installation may require network access. The sample pipeline
does not require it after installation.

### Node

```sh
npm ci --prefix src/node
```

On Windows PowerShell, use `npm.cmd` if execution policy blocks `npm.ps1`:

```powershell
npm.cmd ci --prefix src/node
```

No bundler or TypeScript build step is required.

### Python

Create a virtual environment:

```sh
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on Linux/macOS:

```sh
source .venv/bin/activate
```

Install the runtime and test dependencies used by the project:

```sh
python -m pip install requests pytest jsonschema
```

### Go

Install Go 1.26 (the version in `src/go/go.mod`), then run from the
repository root:

```sh
go -C src/go mod download
go -C src/go test ./...
go -C src/go build -o engine ./cmd/engine
```

Dependency download requires network access unless the modules are already
cached. The CLI looks for `spec/` in the working directory and its parents,
so run it from the repository:

```sh
src/go/engine --workspace workspace --datalake-layout hash --index-backend sqlite \
    split --book-id 8339
src/go/engine --workspace workspace query --terms "says" --mode and
```

Every command and flag is the same as in the Python and Node examples below.
SQLite comes from `modernc.org/sqlite`, a pure-Go translation: no C compiler
is needed.

## Python CLI

Run the Python implementation from the repository root:

```sh
python src/python/cli.py --workspace workspace-python --help
python src/python/cli.py --workspace workspace-python version
```

For the command examples below, replace:

```text
node src/node/cli.js
```

with:

```text
python src/python/cli.py
```

Use a separate Python workspace rather than reusing a Node workspace.

The Python CLI exposes download, split, index, query, lookup, scan-new,
control-step, reconcile and export-canonical. The common command contract
is documented in SPEC §1.

## Node command examples

Run these commands from the repository root after installing dependencies.

Each workspace should use one datalake layout and one index backend.
The examples below use the default `time` layout and `json` backend.
Use a separate workspace when changing either.

### Version

```sh
node src/node/cli.js --workspace workspace version
```

### Download — requires network access

Download one book or every ID in a manifest:

```sh
node src/node/cli.js --workspace workspace download --book-id 8339
node src/node/cli.js --workspace workspace download --manifest data/sample/manifest.txt --workers 2
```

`--source-base` selects an alternative Gutenberg-compatible HTTP source.
Books already marked downloaded are skipped.

### Offline split

The raw input must already exist at `workspace/raw/8339.txt`:

```sh
node src/node/cli.js --workspace workspace split --book-id 8339
```

For a prepared offline workflow, use the one-command demo above.

### Metadata

```sh
node src/node/cli.js --workspace workspace metadata --all
node src/node/cli.js --workspace workspace metadata --book-id 8339
```

Node metadata generation uses ingestion receipts created during ingestion.
Workspaces created without those receipts require an explicit migration or
a new ingestion; timestamps are not fabricated from file modification times.

### Index

```sh
node src/node/cli.js --workspace workspace index --all --positions --batch-size 500
```

`--all` selects downloaded books not yet marked indexed.
The positions setting is fixed when the index is first created.

### Query

```sh
node src/node/cli.js --workspace workspace query --terms "says" --mode and
node src/node/cli.js --workspace workspace query --terms "river mountain" --mode or --limit 10
```

Results contain only book IDs, ascending, one per LF-terminated line.
No matches produce empty stdout. An absent index is an error.

### Lookup

```sh
node src/node/cli.js --workspace workspace lookup --book-id 8339
```

Lookup resolves files through the datalake layout, reads the body and prints
the relative header and body paths separated by a tab.

### Detect pending books

```sh
node src/node/cli.js --workspace workspace scan-new
node src/node/cli.js --workspace workspace scan-new --since 2026-01-01T00:00:00Z
```

### Control step — may access the network

```sh
node src/node/cli.js --workspace control-workspace control-step --manifest data/sample/manifest.txt --iterations 6
```

Each iteration advances one book by one stage: download or index.
The command stops when no candidate remains. This is not the offline demo.

### Reconcile

```sh
node src/node/cli.js --workspace workspace reconcile
```

Reconciliation rebuilds download tracking from complete datalake pairs,
restricts indexed IDs to those books and removes leftover partial files.
It does not reconstruct missing ingestion receipts.

### Export canonical index

```sh
node src/node/cli.js --workspace workspace export-canonical --out canonical.json
```

### Metrics and deterministic ingestion time

```sh
node src/node/cli.js --workspace workspace --metrics-out metrics.jsonl query --terms "says" --mode and
node src/node/cli.js --workspace workspace --now 2026-01-01T00:00:00Z split --book-id 8339
```

Metrics are appended as JSONL and validated against
`spec/schemas/metrics.schema.json`. Wall time uses a monotonic clock.
`version` does not write a metrics record.

See SPEC §1 for the complete common interface and §8 for benchmark
environment variables.

## Tests

Node:

```sh
npm test --prefix src/node
```

Windows PowerShell:

```powershell
npm.cmd test --prefix src/node
```

Python:

```sh
python -m pytest src/python -q
```

The Node suite includes golden-corpus, recovery and filesystem tests.
The folder backend and durability tests can take several minutes.

A SQLite experimental warning may appear with some Node versions.
Check the command's exit code and test results to determine success.

## End-to-end conformance

Run from a Bash shell with Python, Node and dependencies installed:

```sh
bash tools/run_conformance.sh node json time
bash tools/run_conformance.sh node folder book
bash tools/run_conformance.sh node sqlite hash
```

Each invocation checks one combination. To check all nine Node combinations:

```sh
for backend in json folder sqlite; do
  for layout in time book hash; do
    bash tools/run_conformance.sh node "$backend" "$layout" || exit 1
  done
done
```

The script starts a local HTTP mirror of the 20 committed golden books,
downloads through the selected CLI, indexes with positions and compares
the canonical export against `spec/golden/expected.sha256`.
It does not download books from the public Gutenberg website.

Unlike the sample demo, this check deliberately uses a local mirror to
exercise the downloader.

The first argument can be `python`, `node` or `go`.

GitHub Actions includes an implementation in the conformance matrix when
`src/<language>/CONFORMANCE_READY` exists.

## Query parity

With Python and Node dependencies installed:

```sh
node tools/check_query_parity.mjs
```

This compares raw query output bytes across all three index backends and
the four committed query workloads. It also checks the golden export hashes.
The folder runs can take several minutes.

## Benchmarks

Benchmarks run on Linux (WSL2 is fine) on an ext4 path under `~`, never on a
Windows drive. They download from a local mirror of Project Gutenberg, not the
live site. Details, protocol and every experiment: `src/benchmark/runner.py`
(module docstring); results and how to read them: `results/results.md`.

```sh
# one minute, 15 golden books, checks the machinery
python3 src/benchmark/runner.py --smoke

# the real runs (mirror in infra/mirror; run `sudo -v` first so the
# page cache can be dropped before each measured run)
python3 src/benchmark/runner.py --languages python,node,go \
    --experiments E1,E2,E3,E4,E5,E6,E7,E8 --tiers 1000 --backends json,sqlite \
    --reps 3 --warmup 1 --results results/my-run
python3 src/benchmark/runner.py --languages python,node,go \
    --experiments E12 --tiers 100,1000 --results results/my-meta

# micro-benchmarks inside Python (lookup, index queries, metadata queries)
pip install pytest-benchmark
BENCH_TIER=1000 python3 -m pytest src/python/bench --benchmark-only
```

| Experiment | Measures |
|---|---|
| E1 | download, split and store throughput (1 and 8 workers) |
| E2 | lookup of a book by id |
| E3 | detection of new books (`scan-new --since`) |
| E4 | recovery after SIGKILL half-way through a download |
| E5 | storage overhead per datalake layout |
| E6 | index build time, peak memory, index size on disk |
| E7 | query latency (single term, AND-2, AND-3, absent terms) |
| E8 | index update: 50 books added to an indexed tier |
| E12 | metadata database build (batch size 1 and 500) |
| E13 | metadata queries Q1-Q4 of SPEC §5.4 (micro) |

Each run writes `raw.jsonl` (one record per repetition, schema in
`spec/schemas/metrics.schema.json`) and `summary.csv` (median and IQR).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected error |
| 2 | Invalid arguments |
| 3 | Missing book or missing Gutenberg markers |
| 4 | Workspace locked by another writer |

Human-readable diagnostics go to stderr.

## Sample provenance

`data/sample/8339.txt`, `8527.txt`, and `8708.txt` are copied from the
corresponding committed files in `spec/golden/`. Original Gutenberg notices
are retained in the raw files.
