# Stage 1 — Cross-Language Specification (binding contract)

This document is **normative**. If an implementation disagrees with it, the implementation is wrong.
Any change requires a PR labelled `spec-change`, approved by all four members, and a bump of `SPEC_VERSION`.

```
SPEC_VERSION = 1.0.0
```

Every implementation prints its `SPEC_VERSION` under `<engine> version` and refuses to run if it does not
match the value in `spec/SPEC_VERSION`.

Keywords **MUST**, **MUST NOT**, **SHOULD**, **MAY** follow RFC 2119.

---

## 1. Common CLI

All three implementations expose the **same** command surface. The benchmark runner calls this and nothing else.

```
<engine> [GLOBAL FLAGS] <command> [COMMAND FLAGS]

GLOBAL FLAGS
  --workspace <path>          required; root of datalake/datamarts/control
  --datalake-layout <s>       time | book | hash          (default: time)
  --index-backend <s>         json | folder | sqlite | mongo (default: json)
  --metrics-out <path>        append one JSON metrics record per run
  --log-level <s>             error | warn | info | debug (default: info)
  --seed <int>                seed for any randomised choice (default: 42)

COMMANDS
  version                     print SPEC_VERSION and build info, exit 0
  download   --book-id <id> | --manifest <path>  [--workers N] [--source-base <url>]
  split      --book-id <id>                      (offline re-split of a cached raw file)
  metadata   --book-id <id> | --all
  index      --book-id <id> | --all  [--positions] [--batch-size N]
  query      --terms "<t1> <t2> ..." --mode and|or  [--limit N]
  lookup     --book-id <id>                       resolve + read body via datalake layout
  scan-new                                        list ids present in datalake, absent from indexed
  control-step --iterations N [--total-books 70000]
  reconcile                                       repair control files from datalake contents
  export-canonical --out <path>                   emit the canonical index (see §7)
```

**Exit codes.** `0` success · `1` unexpected error · `2` invalid arguments · `3` book not found / markers missing · `4` workspace locked.

**Determinism.** Given the same workspace state and the same flags, every command **MUST** be deterministic. No wall-clock-dependent behaviour except the `YYYYMMDD/HH` datalake path, which **MUST** be overridable via `--now <ISO8601>` for testing.

---

## 2. Ingestion

### 2.1 Fetch

- URL: `{source_base}/cache/epub/{id}/pg{id}.txt`, where `source_base` defaults to `https://www.gutenberg.org` and is overridden to the local mirror during benchmarks.
- Timeout: 30 s connect, 60 s read. Retries: 3, exponential backoff 1 s / 2 s / 4 s, on 5xx and network errors only. **Never retry 404.**
- `User-Agent: ULPGC-BigData-Stage1/<group_name> (+<repo url>)`.
- Response **MUST** be decoded as UTF-8 with replacement of invalid sequences (Gutenberg occasionally serves Latin-1 mislabelled).

### 2.2 Marker detection and split

```
START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER   = "*** END OF THE PROJECT GUTENBERG EBOOK"
```

- Match is a **substring** search, case-sensitive, first occurrence for START, **last** occurrence for END.
  (Using the last END occurrence matters: some books quote the marker inside the text.)
- `header` = text before the first START marker line, with that line excluded.
- `body` = text between the end of the START marker line and the start of the last END marker line.
- `footer` = discarded.
- If either marker is absent → exit code `3`, append `<id>\tNO_MARKERS\t<ISO8601>` to `control/failed_books.txt`, write nothing to the datalake.

### 2.3 Body cleaning (exhaustive — do not add steps)

Applied in this order:

1. Strip UTF-8 BOM if present.
2. `\r\n` → `\n`, then lone `\r` → `\n`.
3. Strip trailing spaces and tabs from every line.
4. Collapse runs of 3+ consecutive newlines to exactly 2.
5. Strip leading and trailing whitespace from the whole string.
6. Ensure the result ends with exactly one `\n`.

The header receives steps 1, 2, 5, 6 only.

### 2.4 Atomic write

```
write <target>.part  →  flush  →  fsync  →  rename to <target>  →  fsync parent dir
```

Only after **all** artifacts for a book are renamed does the id get appended to `control/downloaded_books.txt` (with the file opened in append mode, written, flushed and fsynced).

---

## 3. Tokenizer (the equivalence-critical section)

Specified as a state machine over Unicode **code points**, deliberately *not* as a regex, because `\w`, `\b` and `\p{L}` are not portable across `re` (Python), JavaScript regex and RE2 (Go).

### 3.1 Normalization pipeline

For the whole body text, in this exact order:

1. **NFKC** normalization.
   - Python: `unicodedata.normalize("NFKC", s)`
   - Node: `s.normalize("NFKC")`
   - Go: `golang.org/x/text/unicode/norm.NFKC.String(s)`
2. **Lowercase, locale-invariant.**
   - Python: `s.lower()` · Node: `s.toLowerCase()` (**never** `toLocaleLowerCase()`) · Go: `strings.ToLower(s)`
3. **ASCII folding**: decompose to NFD, drop all code points in category `Mn` (non-spacing marks), recompose to NFC. So `café → cafe`, `naïve → naive`.

### 3.2 Token extraction state machine

Scan the normalized text code point by code point. Maintain a buffer.

- A code point is a **word character** if its Unicode general category is `Lu, Ll, Lt, Lm, Lo` (letters) or `Nd` (decimal digits).
- The apostrophe `U+0027` and `U+2019` are **internal joiners**: they continue a token *only if* the previous and next code points are both word characters. Otherwise they terminate it.
- Any other code point terminates the current token.
- On termination, if the buffer is non-empty, emit it as a token and record its **position** = the 0-based ordinal of this token in the document (not a byte or character offset).

### 3.3 Filtering

Applied to each emitted token, in order:

1. Discard if length < 2 code points.
2. Discard if length > 40 code points (Gutenberg contains OCR garbage strings).
3. Discard if it consists **only** of digits.
4. Discard if present in `spec/stopwords_en.txt` (exact match, one lowercase term per line, `#` comments, UTF-8, LF endings).

> **Important:** positions are assigned **before** filtering, so that token ordinals reflect the true document
> order and phrase queries in Stage 2 remain correct. Filtering removes postings, never renumbers them.

### 3.4 Conformance fixture

`spec/golden/tokens_20.jsonl` contains, for each of the 20 golden books, one line:
`{"book_id": 1342, "n_tokens_raw": 137281, "n_tokens_kept": 61044, "sha256_tokens": "…"}`
where `sha256_tokens` is the SHA-256 of the kept tokens joined by `\n` and UTF-8 encoded.
All three implementations **MUST** reproduce these exactly.

---

## 4. Datalake layouts

`id6` = the `book_id` formatted as a zero-padded 6-digit decimal string (`1342 → "001342"`).

| Layout | Body path | Header path |
|---|---|---|
| `time` | `datalake/<YYYYMMDD>/<HH>/<id>.body.txt` | `datalake/<YYYYMMDD>/<HH>/<id>.header.txt` |
| `book` | `datalake/books/<id>/body.txt` | `datalake/books/<id>/header.txt` |
| `hash` | `datalake/<id6[0:2]>/<id6[2:4]>/<id>.body.txt` | `datalake/<id6[0:2]>/<id6[2:4]>/<id>.header.txt` |

`YYYYMMDD` / `HH` use **UTC**, from the ingestion instant (or `--now`). `HH` is 24-hour, zero-padded.

### 4.1 Required operations per layout

```
write(book_id, header, body) -> (header_path, body_path)
lookup(book_id)              -> (header_path, body_path) | NOT_FOUND
list_new(since)              -> iterable<book_id>
```

**`lookup` MUST NOT consult the metadata store or any auxiliary index.** It resolves using only the layout's own rules and the filesystem. For `time` this means a directory walk; that cost is the measurement, and short-circuiting it invalidates experiment E2. Implementations **MUST NOT** cache walk results between process invocations.

`list_new(since)` for `time` scans only date/hour directories at or after `since`; for `book` and `hash` it walks the whole tree and filters on mtime. This asymmetry is the point of experiment E3.

### 4.2 `book` layout extra artifact

`book` layout additionally writes `datalake/books/<id>/meta.json` — the parsed metadata record (§5.1). This is an intentional, documented advantage of the layout (self-describing unit), and its storage cost is captured by experiment E5.

---

## 5. Metadata datamart

### 5.1 Record schema (`spec/schemas/metadata.schema.json`)

```json
{
  "book_id":      1342,
  "title":        "Pride and Prejudice",
  "author":       "Jane Austen",
  "language":     "en",
  "release_date": "1998-06-01",
  "header_path":  "datalake/20260917/14/1342.header.txt",
  "body_path":    "datalake/20260917/14/1342.body.txt",
  "body_bytes":   704158,
  "sha256":       "…",
  "ingested_at":  "2026-09-17T14:03:11Z"
}
```

Paths are stored **relative to the workspace root**. Absolute paths would break the moment the workspace is copied, and the benchmark runner copies workspaces.

### 5.2 Header parsing rules

The header is a sequence of `Field: value` lines, possibly with continuation lines indented by whitespace.

- Field matching is **case-insensitive** on the field name, anchored at line start.
- A line that starts with whitespace and follows a recognised field is a **continuation**: append it to the previous value separated by a single space.
- `Title` → `title`. Required; if absent, `title = "Unknown"` and a `MISSING_TITLE` warning is logged.
- `Author` → `author`. If absent, `null`. Strip a trailing `, <years>` life-span suffix (e.g. `Austen, Jane, 1775-1817`). Names in `Surname, Given` form are **kept verbatim**; normalising them is a Stage 2 concern and would diverge across implementations.
- `Language` → `language`, mapped to ISO 639-1 via `spec/language_map.txt`; unmapped values are stored lowercased as-is.
- `Release date` / `Release Date` → `release_date`, parsed from Gutenberg's `Month DD, YYYY` form to `YYYY-MM-DD`; if unparseable, `null`. Month names are matched against a **committed English month table**, never a locale-dependent date parser.
- All values: collapse internal whitespace runs to a single space, then trim.

### 5.3 SQLite schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS books (
    book_id      INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL,
    author       TEXT,
    language     TEXT,
    release_date TEXT,
    header_path  TEXT    NOT NULL,
    body_path    TEXT    NOT NULL,
    body_bytes   INTEGER NOT NULL,
    sha256       TEXT    NOT NULL,
    ingested_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_books_author   ON books(author);
CREATE INDEX IF NOT EXISTS idx_books_language ON books(language);
CREATE INDEX IF NOT EXISTS idx_books_title    ON books(title);
```

Inserts **MUST** use `INSERT OR REPLACE` (idempotency, invariant I4) and **MUST** be batched inside a single transaction per `--batch-size` books. Per-row autocommit turns a 3-second job into a 4-minute one; measuring *that* is legitimate, measuring it by accident is not — so `--batch-size` is a benchmark variable, fixed at 500 unless the experiment says otherwise.

### 5.4 Benchmark query set (experiment E7)

```
Q1  SELECT * FROM books WHERE author = ?;
Q2  SELECT body_path FROM books WHERE book_id = ?;
Q3  SELECT * FROM books WHERE title LIKE ? || '%';
Q4  SELECT language, COUNT(*) FROM books GROUP BY language;
```

Parameters are drawn from `spec/queries/metadata_params.txt`, with a fixed seed.

---

## 6. Inverted index backends

### 6.1 `json` — monolithic file

`datamarts/inverted_index.json`, UTF-8, no BOM, LF. Object mapping term → posting list.
Written atomically (`.part` + rename). Updating a single book **MUST** load, merge and rewrite the whole file — that O(N) cost is precisely what the experiment measures, so incremental side-files are forbidden.

### 6.2 `folder` — one file per term

`datamarts/inverted_index/<BUCKET>/<safe_term>.txt`

- `BUCKET` = uppercase first code point of the term if it is `A–Z`, otherwise `_`.
- `safe_term` = the term with every code point outside `[a-z0-9]` percent-encoded as `%XX` of its UTF-8 bytes. (Raw terms as filenames break on case-insensitive filesystems — `Apple` and `apple` collide on macOS/NTFS — and on reserved names. This must be uniform across implementations.)
- File content, one posting per line: `<book_id>\t<tf>\t<pos1>,<pos2>,…\n`, sorted by ascending `book_id`. Without `--positions`, the third column is omitted.
- Update = read file, merge, atomic rewrite of **that file only**.

### 6.3 `sqlite` — embedded B-tree (MANDATORY third structure)

The brief explicitly permits custom approaches. SQLite is chosen over MongoDB as the mandatory third structure
because the dependency already exists for the metadata datamart, drivers are mature in all three languages, and
it needs no external service — which matters given the schedule. It also sharpens the comparison: the three
mandatory backends become *one big file* vs *many small files* vs *an embedded B-tree*, which is a cleaner
axis than file-vs-file-vs-network.

`datamarts/index.db`:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS terms (
    term  TEXT    PRIMARY KEY,
    df    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
    term      TEXT    NOT NULL,
    book_id   INTEGER NOT NULL,
    tf        INTEGER NOT NULL,
    positions TEXT,                     -- comma-separated ascending, NULL when --positions is off
    PRIMARY KEY (term, book_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_postings_book ON postings(book_id);
```

- Inserts use `INSERT OR REPLACE` inside one transaction per `--batch-size` books (idempotency, invariant I4).
- Single-term query: `SELECT book_id, tf FROM postings WHERE term = ? ORDER BY book_id`.
- AND-k query: `SELECT book_id FROM postings WHERE term IN (…) GROUP BY book_id HAVING COUNT(*) = k`.
- `WITHOUT ROWID` is deliberate — it stores the row in the index B-tree itself, halving lookups for this
  access pattern. Mention it in the report's design decisions.

### 6.4 `mongo` — NoSQL (OPTIONAL, stretch goal)

Attempt only after `json`, `folder` and `sqlite` pass conformance in all three languages.

- URI from `MONGO_URI`, default `mongodb://localhost:27017`; database `searchengine_<run_id>`; collection `inverted_index`.
- Document: `{ "_id": "<term>", "df": <int>, "postings": [ {"id":…, "tf":…, "pos":[…]}, … ] }`
- Updates use `bulkWrite` with `updateOne(upsert=true)` in batches of `--batch-size`.
- Write concern `w:1`, journal `false` — stated in the report, because `w:majority` on a single node would make the comparison against local files unfair in the opposite direction.

---

## 7. Canonical export (the equivalence oracle)

`export-canonical --out <path>` emits a byte-identical representation regardless of language and backend:

- JSON, UTF-8, LF, **no** trailing newline, **no** insignificant whitespace (`,` and `:` separators, no spaces).
- Top level: object, keys = terms, sorted by **UTF-8 byte order** (not by locale collation).
- Value: `{"df":<int>,"postings":[[<id>,<tf>,[<pos>,…]],…]}`, postings sorted by ascending `id`, positions ascending.
- Without `--positions`, the third element of each posting triple is omitted: `[<id>,<tf>]`.
- Integers are emitted with no leading zeros and no exponent form.

CI computes `sha256(export-canonical)` for the golden corpus and compares against `spec/golden/expected.sha256`.
**Three languages × three mandatory backends = nine identical hashes, or the build fails.**

---

## 8. Metrics record schema (`spec/schemas/metrics.schema.json`)

One JSON object per line, appended to `--metrics-out`:

```json
{
  "run_id":        "2026-10-02T09-14-22Z-a3f9",
  "spec_version":  "1.0.0",
  "language":      "python",
  "impl_version":  "git:7f3c1ab",
  "experiment":    "E8_index_build",
  "datalake_layout": "hash",
  "index_backend": "folder",
  "positions":     true,
  "corpus_size":   1000,
  "workers":       1,
  "batch_size":    500,
  "repetition":    3,
  "metric":        "wall_time",
  "value":         48213.774,
  "unit":          "ms",
  "aux": {
    "peak_rss_bytes": 813334528,
    "bytes_written":  1043219873,
    "files_created":  312044,
    "dirs_created":   27,
    "docs_processed": 1000,
    "terms_total":    284119
  },
  "machine_id":    "bench-01",
  "started_at":    "2026-10-02T09:14:22.114Z"
}
```

`wall_time` **MUST** come from a monotonic clock: `time.perf_counter_ns()` / `System.nanoTime()` / `time.Now()` with a monotonic reading. `peak_rss_bytes` is measured by the **runner** (via `/usr/bin/time -v` or `getrusage` on the child), not self-reported, so that JVM and Go runtime overhead is counted honestly.

---

## 9. Per-language constraints

| | Python | Node.js | Go |
|---|---|---|---|
| Minimum version | 3.11 | 22 LTS | 1.22 |
| Build / run | `venv` + `pyproject.toml`; entry `python -m engine` | `package.json`, ESM, **no bundler, no TypeScript build step**; entry `node src/cli.js` | `go build ./cmd/engine` |
| JSON | stdlib `json` | stdlib `JSON` | `encoding/json` |
| SQLite | stdlib `sqlite3` | `node:sqlite` (Node 22+) or `better-sqlite3` — **synchronous**, which keeps the code shape identical to the other two | `modernc.org/sqlite` (pure Go — avoids cgo, keeps builds portable) |
| HTTP | `requests` | stdlib `fetch` | stdlib `net/http` |
| Unicode NFKC | stdlib `unicodedata` | `String.prototype.normalize("NFKC")` | `golang.org/x/text/unicode/norm` |
| Concurrency for `--workers` | `ThreadPoolExecutor` (I/O-bound) | N concurrent promises with a bounded pool — **not** `Promise.all` over the whole manifest | goroutines + bounded `errgroup` |
| Lint / format | `ruff` | `eslint` + `prettier` | `gofmt` + `go vet` |
| Mongo *(optional)* | `pymongo` | `mongodb` | `go.mongodb.org/mongo-driver` |

**Node-specific traps** (these will cost hours if discovered during the port, so they are specified here):

- `String.prototype.normalize` exists, but there is **no** built-in NFD-strip-marks step. Implement ASCII folding
  as `s.normalize("NFD").replace(/\p{Mn}/gu, "").normalize("NFC")` — `\p{Mn}` with the `u` flag is supported and
  is the one place a regex is permitted, because its semantics are identical to Python's and Go's category test.
- `toLowerCase()` in JS is already locale-invariant. Do **not** use `toLocaleLowerCase()`.
- JS strings are UTF-16. Iterate with `for (const ch of s)` or `[...s]` to get **code points**, never `s[i]` or
  `charCodeAt`, or astral characters will be split and the conformance hash will diverge.
- Node integers are doubles beyond 2^53. Book IDs and positions are far below that, so plain numbers are fine;
  do not introduce `BigInt`, as its JSON serialisation differs.
- Use `fs.writeFileSync` + `fs.fsyncSync` + `fs.renameSync` for the atomic write protocol (spec §2.4). The async
  API makes ordering guarantees harder to reason about and buys nothing here.

**Warm-up fairness note.** Node's JIT and Go's first-run page faults both need warm-up. Every benchmark discards
the first repetition as warm-up **for all three languages equally**, not selectively. Applying it to one language
only would be a silent thumb on the scale.

**Concurrency fairness note.** `--workers` is a *logical* concurrency level. Go maps it to goroutines, Node to
in-flight promises on a single event-loop thread, Python to OS threads that release the GIL on I/O. These have
genuinely different costs, and that difference is a finding to report, not an artefact to normalise away. What
must be identical is the number of in-flight requests.

---

## 10. Testing requirements (every implementation)

1. **Unit** — tokenizer against `spec/golden/tokens_20.jsonl`; header parser against 10 committed header fixtures including the awkward ones (missing author, multi-line title, non-English).
2. **Conformance** — `export-canonical` hash matches `spec/golden/expected.sha256`, for every index backend.
3. **Invariants** — I1–I4 from `00-ARCHITECTURE.md §3.5`, including a `SIGKILL`-and-resume test.
4. **Round-trip** — `write` then `lookup` returns identical bytes, for all three datalake layouts.

CI matrix: `{python, node, go} × {json, folder, sqlite} × {time, book, hash}` on the 20-book golden corpus. It runs in under two minutes and is the safety net for four people working in parallel.
