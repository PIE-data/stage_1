# Stage 1 — Work Breakdown

Tasks are unassigned. Each one is a GitHub issue: copy the title, the *Files*, the *Done when* and the *Depends on*.

**Size:** S ≤ 2 h · M 2–5 h · L 5–10 h · XL > 10 h
**`×3`** = one issue per language (Python, Node, Go). Total issue count: 31 base + 22 port = **53**.

---

## 0. Structure of the work

Five phases. Phase 0 gates everything; Phases 3–4 can start before Phase 2 finishes.

```
P0 FOUNDATIONS          spec + shared assets + repo/CI
   │                    nothing else starts until the spec is frozen
   ▼
P1 REFERENCE            one language, end to end
   │                    ends by FREEZING the golden fixture — the oracle for P2
   ▼
P2 PORTS                the other two languages, built from the spec only
   │                    ends at 9 identical canonical hashes
   ▼
P3 BENCHMARKS           harness can be built during P1; runs need P2
   ▼
P4 REPORT + DELIVERY    §3 and §4 can be written during P1
```

**The critical path is P0 → tokenizer → index backends → canonical freeze → ports → conformance → benchmarks.**
Everything else (metadata, control layer, downloader, report prose, bench harness) runs alongside it.

### Four parallel tracks

Assign one person per track. Tracks are sized to finish together.

| Track | Phase 0–1 | Phase 2 | Phase 3–4 |
|---|---|---|---|
| **T1 — Spec & core** | spec, shared assets, tokenizer, JSON index, SQLite index, canonical export, conformance CI | unblocks both ports; fallback if one stalls | report §3, §4; final checklist |
| **T2 — Datalake** | downloader, splitter, 3 storage layouts | full port, language A | sample dataset; §5 contribution |
| **T3 — Datamarts** | metadata parser + store, folder index, query | full port, language B | §5 contribution |
| **T4 — Infra & analysis** | repo, CI, corpus mirror, control layer, bench harness | — | all benchmark runs, plots, §2, §5, §6, README |

T1 must be the person with the most availability: they own the oracle everyone else is blocked on, and they are the fallback for whichever port slips.

---

## Phase 0 — Foundations

**Gate: nothing in `src/` merges until T05 is tagged `spec-frozen`.**

| ID | Task | Size | Files | Done when | Depends on |
|---|---|---|---|---|---|
| T01 | Rename repo `PIE-search` → `stage_1` | S | — | URL is exactly `github.com/PIE-data/stage_1` (brief §6.2) | — |
| T02 | Protect `main`, add CODEOWNERS, issue labels, project board | S | `.github/` | PR + 1 approval required; direct push blocked | T01 |
| T03 | Make org membership public; verify each `git config user.email` | S | — | contributor graph shows all four | T01 |
| T04 | Corpus mirror — fetch candidates at 1 req/s, validate | M | `infra/mirror/` | ≥10 000 books cached, each with both markers and `Language: English`; ~7 h unattended | — |
| T05 | **Freeze `SPEC.md`** — all four approve line by line | L | `docs/SPEC.md` | tag `spec-frozen` | — |
| T06 | Shared assets: stop-word list, corpus manifests 100/500/2000 | M | `spec/stopwords_en.txt`, `spec/corpus/` | manifests validated against the mirror, committed, immutable | T04, T05 |
| T07 | Query workloads: 100 high-freq, 100 mid, 100 rare, **100 absent** | S | `spec/queries/` | drawn from the real corpus vocabulary, fixed seed | T06 |
| T08 | Golden corpus: 20 books vendored into the repo | S | `spec/golden/` | tests run with no network | T04 |
| T09 | CI skeleton: matrix over `src/{python,node,go}`, lint + conformance job | M | `.github/workflows/ci.yml` | runs and fails *deterministically*, not with errors | T02 |
| T10 | Language skeletons that build and print `version` `×3` | S | `src/<lang>/cli.*` | CI executes all three | T09 |

---

## Phase 1 — Reference implementation

One language, end to end. Do **not** start the ports before T20.

| ID | Task | Size | Files | Done when | Depends on |
|---|---|---|---|---|---|
| T11 | Downloader — retries, backoff, never retry 404, mirror-aware `--source-base` | M | `datalake/downloader.py` | 20 golden books fetch from the mirror | T05 |
| T12 | Splitter + body cleaner — markers are `***`, **three** asterisks; first START, **last** END | M | `datalake/splitter.py` | 20 golden books split with byte-identical header/body; missing marker → exit 3 + `failed_books.txt` | T11 |
| T13 | Atomic write protocol — `.part` → fsync → rename → fsync dir, *then* append to control | S | `datalake/atomic.py` | kill between rename and append leaves a recoverable state | T12 |
| T14 | `TimeBasedStorage` | M | `datalake/time_storage.py` | `write`→`lookup` round-trip returns identical bytes | T13 |
| T15 | `BookBasedStorage` | S | `datalake/book_storage.py` | idem, plus `meta.json` written | T13 |
| T16 | `BatchBasedStorage` — **digit prefix `id6[0:2]/id6[2:4]`, not a hash function** | S | `datalake/batch_storage.py` | idem; directory fan-out bounded | T13 |
| T17 | Header parser — regex, continuation lines, life-span stripping, month table | M | `datamart/metadata.py` | 10 committed header fixtures parse correctly, incl. missing author and multi-line title | T05 |
| T18 | SQLite metadata store — schema, indexes, batched upsert | M | `datamart/metadata.py` | Q1–Q4 return correct rows; re-running writes nothing new | T17 |
| T19 | **Tokenizer + normalizer + stop words** — state machine, not a regex | L | `core/tokenizer.py` | generates `spec/golden/tokens_20.jsonl`; **then frozen** | T06 |
| T20 | `JsonIndex` | M | `datamart/index_json.py` | canonical hash reproducible across runs | T19 |
| T21 | `FolderIndex` — percent-encode chars outside `[a-z0-9]`, bucket by first letter or `_` | M | `datamart/index_folder.py` | hash matches T20 | T19 |
| T22 | `SqliteIndex` — `terms` + `postings WITHOUT ROWID` | M | `datamart/index_sqlite.py` | hash matches T20 | T19 |
| T23 | **`export_canonical` + freeze `expected.sha256`** | M | `core/canonical.py` | **3 backends → 1 identical hash**; fixture frozen | T20, T21, T22 |
| T24 | `query --terms --mode and\|or` | M | `cli.py` | identical id list from all 3 backends | T20, T21, T22 |
| T25 | Control layer — `StateTracker`, `control_pipeline_step`, `failed_books.txt`, `run.lock` | L | `core/control_layer.py` | invariants I1–I4 pass (below) | T14–T16 |
| T26 | `reconcile` — rebuild control files from the datalake | S | `core/control_layer.py` | recovers a crash between rename and append | T25 |
| T27 | `--metrics-out` instrumentation — monotonic clock, JSONL schema | M | `cli.py` | one record per run, schema-validated | T25 |

### Control-layer invariants (the acceptance criteria for T25)

| | Invariant | Test |
|---|---|---|
| I1 | No duplicates | an id appears at most once per control file after 1 000 steps |
| I2 | No loss | every id in `downloaded_books.txt` has readable body + header |
| I3 | Crash safety | `SIGKILL` at 50%, restart, converges to the uninterrupted final state |
| I4 | Idempotency | re-running on a complete corpus performs zero writes |

---

## Phase 2 — Ports

Two languages. **Built from `SPEC.md`, never by reading the reference source** — copying the reference hides spec bugs and makes the three implementations agree by accident, which is exactly what the conformance test exists to catch.

Each port is the same seven issues. Do them in this order; each ends with a green sub-test.

| ID | Task | Size | Done when | Depends on |
|---|---|---|---|---|
| T28 `×2` | Port: tokenizer | L | output matches `spec/golden/tokens_20.jsonl` exactly | T19 |
| T29 `×2` | Port: downloader + splitter + atomic write | M | 20 golden books split byte-identically | T12 |
| T30 `×2` | Port: 3 datalake storages | M | round-trip passes for all 3 layouts | T14–T16 |
| T31 `×2` | Port: metadata parser + store | M | 10 header fixtures parse identically | T18 |
| T32 `×2` | Port: 3 index backends + `export_canonical` | XL | **3 identical hashes**, matching `expected.sha256` | T23, T28 |
| T33 `×2` | Port: control layer + `reconcile` | M | I1–I4 pass | T26 |
| T34 `×2` | Port: `query` + `--metrics-out` | M | identical id lists; metrics schema-valid | T24, T27 |
| T35 | **Conformance gate: CI matrix 3 langs × 3 backends × 3 layouts green** | M | **9 identical canonical hashes** — tag `conformance-green` | all T32 |

> **Node traps** (all specified in `SPEC.md`, listed here because they are what actually breaks the hash):
> iterate code points with `for (const ch of s)` — never `s[i]`, JS strings are UTF-16 and astral characters split;
> `toLowerCase()`, never `toLocaleLowerCase()`; sort canonical keys on **UTF-8 bytes**, not the native `<`;
> use the sync `fs` API for the atomic write protocol.

---

## Phase 3 — Benchmarks

T36 can be built during Phase 1 against a stub. The runs need T35.

| ID | Task | Size | Files | Done when | Depends on |
|---|---|---|---|---|---|
| T36 | Bench runner — repetitions, warm-up discard, cold-cache drop, **external** RSS capture, disk/inode preflight (abort under 60 GB free or 4 M free inodes), teardown excluded from the timer | L | `src/benchmark/runner.py` | dry run on `manifest_100` completes for all 3 languages | T27 |
| T37 | E1–E5 datalake benchmarks | M | `src/benchmark/datalake_bench.py` | every (language × layout × metric) cell filled | T35, T36 |
| T38 | E6–E9 index benchmarks | M | `src/benchmark/index_bench.py` | every (language × backend × metric) cell filled | T35, T36 |
| T39 | E10–E11 scalability sweep, 100 / 1 000 / 10 000 | M | — | run in one unattended session; the `folder` backend at 10 000 creates ~1 M files, so budget for slow teardown | T37, T38 |
| T40 | Aggregate + plot F1–F10 | M | `aggregate.py`, `plots.py` | `results/summary.csv` and all figures committed | T39 |
| T41 | Check results against the written predictions; investigate every contradiction | M | — | each anomaly explained in prose or filed as a bug | T40 |

### Experiment matrix

| ID | Metric (brief's wording) | Varies | Fixed |
|---|---|---|---|
| E1 | Download and write throughput | lang × layout | corpus 1 000, workers ∈ {1, 8} |
| E2 | Lookup cost | lang × layout | 1 000 seeded lookups, p50 / p95 |
| E3 | Incremental processing | lang × layout | corpus 1 000, +50 new |
| E4 | Recovery behavior | lang × layout | SIGKILL at 50%; count duplicates and losses |
| E5 | Storage overhead | layout only | #files, #dirs, bytes, bytes/book |
| E6 | Indexing speed | lang × backend | layout `batch`, corpus 1 000, positions on |
| E7 | Query performance | lang × backend | 4 workloads: single, AND-2, AND-3, **absent** |
| E8 | Update performance | lang × backend | +50 books onto a 1 000-book index |
| E9 | Memory and disk usage | lang × backend | peak RSS, on-disk bytes, #files |
| E10 | Scalability — datalake | layout × size | one language |
| E11 | Scalability — index | backend × size | one language |

> **Cost of the 10 000-book tier.** The `folder` backend creates roughly one file per distinct
> term — on the order of a million. Deleting that tree between repetitions takes minutes, so teardown
> must sit outside the timer, and the run belongs on ext4: NTFS and APFS are case-insensitive and would
> silently merge terms that differ only in case.

### Protocol (non-negotiable — without these the numbers are noise)

- One machine for any set that appears in the same chart; record CPU, RAM, **filesystem type**, disk type, OS.
- **Linux or WSL2 on ext4.** `FolderIndex` silently corrupts on case-insensitive filesystems (macOS, Windows).
- 3 repetitions, **discard the first** as warm-up, applied to all three languages equally.
- Report **median + IQR**, never mean ± stdev — these distributions are right-skewed.
- Clean workspace per run; **teardown excluded from the timer** (deleting a 200 k-file index takes minutes).
- Cold cache is primary (`drop_caches`); warm is a secondary column. Decide the no-root fallback before running.
- All fetches hit the local mirror. One live-network run, reported separately, quantifies the bias.
- Peak RSS measured by the runner on the child process, never self-reported.

---

## Phase 4 — Report and delivery

§3 and §4 need no benchmark results — write them during Phase 1, while the decisions are fresh.

| ID | Task | Size | Files | Done when | Depends on |
|---|---|---|---|---|---|
| T42 | Report skeleton + cover page | S | `report/main.tex` | course name **+ academic year**, title, **full names + student IDs**, group name, repo URL | — |
| T43 | §2 Introduction and objectives | M | — | scoped to Stage 1 | — |
| T44 | §3 System architecture — datalake, datamart **and control layer** | M | — | all three covered | T25 |
| T45 | §4 Design decisions — structures and indexing strategies, **justified** | M | — | each choice in the table below argued | T23 |
| T46 | §5 Benchmarks and results | L | — | ≥3 languages · datalake structure · index structure · **which layout we chose and why** · **cross-language trade-offs** | T41 |
| T47 | §5 *Threats to validity* subsection | S | — | single machine, one filesystem, English-only corpus, 10 000 books is still a modest corpus, mirror removes network variance, 3 reps, SQLite in place of Mongo | T46 |
| T48 | §6 Conclusions and future improvements | S | — | names what Stage 2 inherits | T46 |
| T49 | `README.md` — detailed setup and execution | M | `README.md` | a teammate who did **not** write it runs the pipeline clean from it | T35 |
| T50 | `data/sample/` — small committed dataset | S | `data/sample/` | instructor runs the pipeline in one command | T35 |
| T51 | Git-history review — does it show progression? | S | — | steady commits across the whole period, not a final-night dump | — |
| T52 | Final pass against the delivery checklist | M | — | every box below ticked | all |
| T53 | Build the PDF; **one member** uploads to the virtual campus | S | — | submitted | T52 |

---

## Delivery checklist

**Report** — `.pdf`, the only file submitted, uploaded by exactly one member:

- [ ] Cover: course name **and academic year**
- [ ] Cover: project title
- [ ] Cover: **full names and student IDs** of all four members
- [ ] Cover: chosen group name
- [ ] Cover: GitHub repository URL
- [ ] §2 Introduction and objectives
- [ ] §3 System architecture — datalake, datamart **and control layer**
- [ ] §4 Design decisions — **justified**
- [ ] §5 ≥3 programming languages compared
- [ ] §5 datalake structure evaluated
- [ ] §5 inverted-index structure evaluated
- [ ] §5 explicit justification of the datalake structure **selected for the final implementation**
- [ ] §5 explicit discussion of trade-offs **across the programming languages**
- [ ] §6 Conclusions and future improvements

**Repository:**

- [ ] URL is exactly `github.com/<group_name>/stage_1`
- [ ] `README.md` with detailed setup and execution instructions
- [ ] Sample dataset for quick instructor testing
- [ ] Git history shows the progression of the work
- [ ] Public, and the URL in the report resolves

**Implementation:**

- [ ] 3 datalake structures implemented and benchmarked
- [ ] 3 inverted-index structures implemented and benchmarked
- [ ] 3 languages — same dataset, same preprocessing, **equivalent outputs** (9 identical hashes)
- [ ] Metadata parsed from the header into a queryable database
- [ ] **Stop words excluded** from the index *(deck, Indexer slide)*
- [ ] Index stores documents **and positions** *(deck, Indexer slide)*
- [ ] Working `query` command — graded under §7 even though §1 defers querying to Stage 2
- [ ] Control layer with `downloaded_books.txt` and `indexed_books.txt`, no duplicated work

---

## Scope decisions to defend in §4

| Decision | Argument |
|---|---|
| Python + Node + Go | No build ceremony on any of the three; contrast spans interpreted / event-loop JIT / compiled |
| `json` + `folder` + **SQLite** instead of MongoDB | Brief §4.2 explicitly permits custom approaches; no external service; cleaner axis — one big file vs many small files vs embedded B-tree |
| English-only corpus, tiers 100 / 1 000 / 10 000 | One stop-word list. The brief asks for scalability *"from hundreds to tens of thousands"* (§4.1); 10 000 meets it literally. Mirror ≈ 3.5 GB |
| **No stemming** | Porter/Snowball ports are not byte-identical across languages and would break the conformance hash |
| Word-level index (positions + `tf`) | The deck requires positions; Stage 2 needs them for phrase queries and ranking |
| Local mirror for all benchmarks | Live Gutenberg measures their rate limiter, not our code |
| Metadata storage comparison dropped | Explicitly optional in the brief; listed as future work |

---

## Cut order if you fall behind

Cut from the top. Everything below the line is mandatory.

1. Scalability drops to two sizes (100 / 1 000)
2. E3 and E8 run in one language only, extrapolated and stated as such
3. Corpus tiers drop to 100 / 500
4. Optional oral presentation
5. `folder` backend in one language only — *last resort; it costs the three-language index claim*

— **never cut:** 3 languages conformance-verified · 3 datalake structures + E1–E5 · 3 index structures + E6–E9 · control layer with recovery · working `query` · 6-section report · README + sample dataset · **the conformance test** —

Cutting the conformance test to save time is the one change that looks cheap and is not: without it there is no evidence the three implementations are comparable, and the whole of §5 rests on that claim.

---

## Process

- `main` protected: PR + 1 approval. No direct pushes.
- Branches: `<area>/<short-desc>` — `datalake/storages`, `go/tokenizer`, `bench/runner`.
- Conventional Commits: `feat(go): add batch storage`, `fix(python): use last END marker`.
- Every PR names the spec section it implements and the test that proves it.
- Small PRs. A 2 000-line PR gets rubber-stamped, which defeats review.
- Spec questions are GitHub issues labelled `spec-question`, never chat — they become §4 of the report.
- Tags: `spec-frozen` (T05) · `conformance-green` (T35) · `code-freeze` (T41) · `submitted` (T53).
- **Definition of Done:** code + unit tests + conformance passing + PR reviewed + spec updated if it moved.
