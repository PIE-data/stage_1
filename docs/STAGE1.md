# Stage 1 — Structure

Organised section by section against the brief. Each Part: what is required → what we build → files → done-when.

**Repo:** `github.com/PIE-data/stage_1` · **Group:** PIE data · **Languages:** Python (reference), Node, Go
**Task breakdown:** `TASKS.md` · **Binding contract:** `SPEC.md`

---

## Repo layout

```
stage_1/
├── README.md                       setup + run instructions        [graded §6.2]
├── docs/
│   ├── STAGE1.md                   this file
│   ├── TASKS.md                    who does what
│   └── SPEC.md                     binding cross-language contract
├── spec/                           SHARED — identical input for all 3 languages
│   ├── stopwords_en.txt
│   ├── corpus/manifest_{100,1000,10000}.txt
│   ├── queries/{single,and2,and3,absent}.txt
│   └── golden/                     20 books + tokens_20.jsonl + expected.sha256
├── src/
│   ├── python/                     reference
│   │   ├── datalake/               downloader.py, splitter.py, {time,book,batch}_storage.py
│   │   ├── datamart/               metadata.py, index_json.py, index_folder.py, index_sqlite.py
│   │   ├── core/                   tokenizer.py, control_layer.py, canonical.py
│   │   └── cli.py
│   ├── node/                       same module names, .js
│   └── go/                         same module names, .go
├── src/benchmark/                  language-neutral runner
│   ├── runner.py  aggregate.py  plots.py
│   ├── datalake_bench.py  index_bench.py
├── data/sample/                    small committed dataset            [graded §6.2]
├── results/                        raw metrics JSONL + summary.csv
└── report/                         main.tex + figures/  →  report.pdf
```

**Runtime workspace** (never committed, `--workspace <path>`):

```
<workspace>/datalake/  <workspace>/datamarts/  <workspace>/control/
```

---

## Part 2 — Data Source

| | |
|---|---|
| **Required** | Fetch `.txt` from Gutenberg; split into header / body / footer using markers |
| **Files** | `src/<lang>/datalake/downloader.py`, `splitter.py` |

**URL:** `{base}/cache/epub/{id}/pg{id}.txt` — `base` defaults to Gutenberg, overridden to the local mirror for benchmarks.

**Markers — exactly three asterisks, not four:**

```python
START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER   = "*** END OF THE PROJECT GUTENBERG EBOOK"
```

**Interface**

```python
download(book_id: int, base_url: str) -> str                  # raw text, retries 3x backoff, never retry 404
split(raw: str) -> tuple[str, str] | None                     # (header, body); None if a marker is missing
clean_body(body: str) -> str                                  # 6 steps, SPEC.md §2.3
```

Rules: first occurrence of START, **last** of END (some books quote the marker). Footer discarded. Missing marker → exit 3, append to `control/failed_books.txt`, write nothing.

**Done when:** the 20 golden books split with byte-identical header/body in all 3 languages.

---

## Part 3 — Organizing the Datalake

| | |
|---|---|
| **Required** | `datalake/YYYYMMDD/HH/<BOOK_ID>.body.txt` + `.header.txt`, UTC |
| **Files** | `src/<lang>/datalake/{time,book,batch}_storage.py` |

**Interface (one, implemented three times)**

```python
class DatalakeStorage(Protocol):
    def write(self, book_id: int, header: str, body: str) -> tuple[str, str]: ...   # returns (header_path, body_path)
    def lookup(self, book_id: int) -> tuple[str, str] | None: ...
    def list_new(self, since: datetime) -> Iterable[int]: ...
```

`id6` = book_id zero-padded to 6 digits.

| Class | Path |
|---|---|
| `TimeBasedStorage` | `datalake/<YYYYMMDD>/<HH>/<id>.body.txt` |
| `BookBasedStorage` | `datalake/books/<id>/body.txt`, `header.txt`, `meta.json` |
| `BatchBasedStorage` | `datalake/<id6[0:2]>/<id6[2:4]>/<id>.body.txt` — **digit prefix, not a hash** |

**Atomic write:** `write <t>.part → fsync → rename → fsync dir`, *then* append the id to `control/downloaded_books.txt`. This ordering is what makes recovery work.

**`lookup` must not consult the metadata DB.** For `time` that means a real directory walk — that cost is the measurement (E2).

**Done when:** round-trip `write`→`lookup` returns identical bytes for all 3 layouts in all 3 languages.

---

## Part 3.1 — Datalake Structure Benchmark

| | |
|---|---|
| **Required** | ≥3 structures, ≥3 languages, same dataset/rules/outputs; 5 named metrics; report justifies the choice |
| **Files** | `src/benchmark/datalake_bench.py` |

| ID | Metric (brief's wording) | Varies | Fixed |
|---|---|---|---|
| E1 | Download and write throughput | lang × layout | corpus 1 000, workers ∈ {1,8} |
| E2 | Lookup cost | lang × layout | 1 000 seeded lookups, p50/p95 |
| E3 | Incremental processing | lang × layout | corpus 1 000, +50 new |
| E4 | Recovery behavior | lang × layout | SIGKILL at 50%, count duplicates/losses |
| E5 | Storage overhead | layout only | corpus 1 000; #files, #dirs, bytes, bytes/book |
| E10 | Scalability | layout × {100, 1 000, 10 000} | lang = go |

**Protocol:** 3 repetitions, discard the first; median + IQR; one machine, Linux/ext4; clean workspace per run with teardown **excluded** from the timer; local mirror, not live Gutenberg.

**Done when:** `results/summary.csv` has every (lang, layout, metric) cell filled.

---

## Part 4.1 — Datamart: Metadata

| | |
|---|---|
| **Required** | Parse from the **header** with regex; store in a DB; filter by author/title/language; locate the body path |
| **Files** | `src/<lang>/datamart/metadata.py` |

**Interface**

```python
parse_header(header: str) -> dict          # book_id, title, author, language, release_date
class MetadataStore:
    def upsert(self, records: list[dict]) -> None      # batched, INSERT OR REPLACE
    def by_id(self, book_id: int) -> dict | None
    def by_author(self, author: str) -> list[dict]
```

**Schema** (`datamarts/metadata.db`, WAL):

```sql
CREATE TABLE books (
  book_id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT, language TEXT,
  release_date TEXT, header_path TEXT NOT NULL, body_path TEXT NOT NULL,
  body_bytes INTEGER, sha256 TEXT, ingested_at TEXT);
CREATE INDEX idx_author ON books(author);
CREATE INDEX idx_language ON books(language);
CREATE INDEX idx_title ON books(title);
```

Paths stored **relative to the workspace**. Continuation lines (an indented line after `Title:`) append to the previous field. `Author: Austen, Jane, 1775-1817` → strip the trailing life-span, keep `Austen, Jane`.

**Done when:** 10 committed header fixtures (missing author, multi-line title, odd date) parse identically in all 3 languages.

*Storage comparison across SQLite/Mongo/Postgres is optional in the brief → out of scope, listed as future work.*

---

## Part 4.2 — Datamart: Inverted Index

| | |
|---|---|
| **Required** | ≥3 structures, ≥3 languages, same tokenizer/normalisation/query workload; 5 named metrics |
| **Files** | `src/<lang>/datamart/index_{json,folder,sqlite}.py`, `src/<lang>/core/tokenizer.py` |

**Tokenizer** — the equivalence-critical piece. Specified as a state machine over code points, **not a regex** (`\w`/`\p{L}` differ across Python `re`, JS and Go RE2).

```
NFKC → lowercase (locale-invariant) → strip NFD combining marks → NFC
token = run of [Lu Ll Lt Lm Lo Nd]; ' and ’ join only between two word chars
drop: len < 2 · len > 40 · all-digits · in spec/stopwords_en.txt      [deck: "stop words must be avoided"]
position = token ordinal, assigned BEFORE filtering
```

**Interface (one, implemented three times)**

```python
class InvertedIndex(Protocol):
    def add(self, book_id: int, tokens: list[tuple[str, int]]) -> None: ...
    def query(self, terms: list[str], mode: Literal["and","or"]) -> list[int]: ...
    def export_canonical(self, path: str) -> None: ...
```

| Class | Location | Update cost |
|---|---|---|
| `JsonIndex` | `datamarts/inverted_index.json` | rewrites the whole file — that is the point |
| `FolderIndex` | `datamarts/inverted_index/<A-Z\|_>/<term>.txt` | one file per term; percent-encode chars outside `[a-z0-9]` |
| `SqliteIndex` | `datamarts/index.db`, `terms` + `postings` | our custom approach (brief §4.2 permits them) |

Posting: `{book_id, tf, positions[]}` — **word-level**, the deck requires positions.

**Canonical export** (the equivalence oracle): JSON, UTF-8, LF, no whitespace, keys sorted by **UTF-8 bytes**, postings by ascending id. CI asserts `sha256 == spec/golden/expected.sha256` for **3 langs × 3 backends = 9 identical hashes**.

| ID | Metric | Varies |
|---|---|---|
| E6 | Indexing speed | lang × backend |
| E7 | Query performance | lang × backend × {single, and2, and3, **absent**} |
| E8 | Update performance | lang × backend, +50 books onto 1 000 |
| E9 | Memory and disk usage | lang × backend, peak RSS + bytes + #files |
| E11 | Scalability | backend × {100, 1 000, 10 000}, lang = go |

**Done when:** 9 identical hashes in CI.

---

## Part 5 — Control Layer

| | |
|---|---|
| **Required** | `control/downloaded_books.txt`, `control/indexed_books.txt`; index pending first, else download new; no duplicates |
| **Files** | `src/<lang>/core/control_layer.py` |

```python
class StateTracker(Protocol):
    def is_downloaded(self, book_id: int) -> bool: ...
    def is_indexed(self, book_id: int) -> bool: ...
    def mark_downloaded(self, book_id: int) -> None: ...
    def mark_indexed(self, book_id: int) -> None: ...
    def ready_to_index(self) -> set[int]: ...          # downloaded − indexed

def control_pipeline_step() -> None: ...
```

Files: `downloaded_books.txt`, `indexed_books.txt`, plus **our additions** `failed_books.txt` (`id\treason\tISO8601`) and `run.lock` (single-writer guard).

**Invariants — these are the tests:**

| | Invariant |
|---|---|
| I1 | No duplicates: an id appears at most once per control file |
| I2 | No loss: every id in `downloaded_books.txt` has readable artifacts |
| I3 | Crash safety: SIGKILL + restart converges to the same state |
| I4 | Idempotency: re-running on a complete corpus performs zero writes |

`reconcile` rebuilds the control files from the datalake — the recovery path for a crash between rename and append.

**Done when:** the SIGKILL-and-resume test passes for all 3 layouts, in all 3 languages.

---

## Part 5b — Query (not in §1, but graded under §7)

§1 defers querying to Stage 2. **§7 grades it now**: *"proper functioning of downloading, indexing, and querying modules."* Implement the CLI path; no REST API, no UI.

```
engine query --terms "island shipwreck" --mode and --index-backend folder
```

**Done when:** returns the same id list from all 3 backends in all 3 languages.

---

## Part 6 — Delivery

| | Done when |
|---|---|
| **6.1** Report `.pdf`, 6 sections | checklist below all ticked |
| **6.2** Repo `github.com/PIE-data/stage_1` | **renamed from `PIE-search`** |
| **6.2** `README.md`, detailed setup + run | a teammate who did not write it runs it clean |
| **6.2** `data/sample/` | instructor can run the pipeline in one command |
| **6.2** Git history shows progression | steady commits, not a final-night dump |
| **6** One member uploads | submitted |

**Report sections — verify literally:**

- [ ] Cover: course name **+ academic year** · title · **full names + student IDs** · group name **PIE data** · repo URL
- [ ] §2 Introduction and objectives
- [ ] §3 System architecture — datalake, datamart **and control layer**
- [ ] §4 Design decisions — data structures and indexing strategies, **justified**
- [ ] §5 Benchmarks — ≥3 languages · datalake structure · inverted-index structure · **which layout we chose and why** · **cross-language trade-offs**
- [ ] §6 Conclusions and future improvements

Source is `report/main.tex`. **`report.pdf` is a build artifact — never edit it directly.**

---

## Part 7 — Evaluation

| Criterion | % | What earns it here |
|---|---|---|
| Report quality | 30 | 6 sections; §3/§4 written from this file during Phase 1, before benchmarks exist; generated figures; a *Threats to validity* subsection |
| Pipeline correctness | 30 | conformance suite; I1–I4 incl. SIGKILL test; CI matrix 3×3×3; working `query` |
| Code quality | 20 | one interface per storage dimension, three implementations; small reviewed PRs; lint in CI |
| Benchmarking | 20 | E1–E11; predictions written before running; median/IQR over 3 reps; cold-cache |

*Optional: 4-minute presentation per student — extra credit, material already in hand.*

---

## Scope decisions (state these in §4 of the report)

| Decision | Reason |
|---|---|
| Python + **Node** + Go | Node has no build step; saves ~2 person-days vs Java |
| json + folder + **SQLite** (not Mongo) | Brief permits custom approaches; no external service; cleaner axis: one file vs many files vs B-tree |
| Corpus **English only**, 100 / 1 000 / 10 000 | One stop-word list. The brief asks for scalability *"from hundreds to tens of thousands"* (§4.1), so 10 000 is the tier that meets it literally; mirror ≈ 3.5 GB |
| **No stemming** | Porter/Snowball ports are not byte-identical across languages; would break the conformance hash |
| Local mirror for benchmarks | Live Gutenberg measures their rate limiter, not our code; one live run quantifies the bias |
| Metadata storage comparison dropped | Explicitly optional in the brief |
