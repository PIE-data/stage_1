#!/usr/bin/env bash
# Open every issue phases 2-4 still need.  Run once, from the repository root:
#
#     bash tools/create-issues-rest.sh
#
# Requires the GitHub CLI, authenticated:  gh auth status
#
# Companion to tools/create-issues.sh, which opened the phase-0/1 set and the
# two tokenizer ports.  This one covers what is left: the remaining six port
# steps in each language, the conformance gate, the benchmark runs and the
# report sections.
#
# Safe to re-run: an issue whose exact title already exists is skipped, so a
# half-finished run can simply be started again.
#
# Issues stay short -- context, acceptance criteria, references.  docs/SPEC.md
# is the specification and is not duplicated here: an issue that restates the
# spec is a second copy of the spec, and the two drift.
set -euo pipefail

command -v gh >/dev/null || { echo "gh not found: https://cli.github.com"; exit 1; }

ANDREA=andreapatruno1
IVAN=ivanc25
MANUEL=maneloco
MARCELA=marcelakuczynska

for l in node go bench report python infra spec phase-2 phase-3 phase-4; do
  gh label create "$l" --force >/dev/null 2>&1 || true
done

exists () {  # exists <title>
  gh issue list --state all --limit 200 --json title --jq '.[].title' | grep -Fxq "$1"
}

new () {  # new <title> <assignee> <milestone> <labels> <body>
  if exists "$1"; then
    echo "skip (already open): $1"
    return 0
  fi
  if ! gh issue create --title "$1" --assignee "$2" --milestone "$3" --label "$4" --body "$5"; then
    # A missing milestone must not abort the whole run.
    gh issue create --title "$1" --assignee "$2" --label "$4" --body "$5"
  fi
}

PORT_RULE="**Write it from docs/SPEC.md, not from the Python source.**
Reading the reference makes the two implementations agree by accident: the
conformance hash then passes even where the spec is ambiguous or wrong, which
is precisely what it exists to catch. If the spec is unclear, open an issue
labelled \`spec-question\` -- those answers become §4 of the report.

Note SPEC_VERSION is **1.1.0**. Section §1.1 (query semantics and output) was
added on 22 Sep and is normative."

# ===================================================================== ports
# Six steps per language, in dependency order.  Each ends with a green test.

for lang in node go; do
  if [ "$lang" = node ]; then who=$IVAN; L=Node; lab="node,phase-2"; else who=$MANUEL; L=Go; lab="go,phase-2"; fi

  new "Port: downloader + splitter + atomic write to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**Done when**
- the 20 books in \`spec/golden/manifest_20.txt\` split byte-identically to the Python output
- a missing marker exits \`3\` and appends to \`control/failed_books.txt\`
- the atomic write protocol is \`.part\` -> fsync -> rename -> fsync parent dir

**References:** SPEC.md §2.1-§2.4."

  new "Port: three datalake storages to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**Done when**
- \`write\` then \`lookup\` returns identical bytes, for \`time\`, \`book\` and \`hash\`
- \`lookup\` resolves from the layout rules and the filesystem only -- never from the metadata
  store, and never caching a directory walk between runs (it would invalidate experiment E2)
- \`book\` also writes \`meta.json\`

**Remember:** \`hash\` is the digit prefix \`id6[0:2]/id6[2:4]\`, **not** a hash function.

**References:** SPEC.md §4-§4.2."

  new "Port: metadata parser + SQLite store to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**Done when**
- the 10 committed header fixtures parse identically to the Python output
- re-running writes nothing new (invariant I4)

**Trap found in the real corpus:** a header field must be matched against the known field list
(Title, Author, Editor, Illustrator, Translator, Release date, Language, Credits) at column 0.
Accepting any \`word:\` line turns the \`http://...\` inside Credits into a field named \"http\",
on roughly half the corpus.

**References:** SPEC.md §5.1-§5.3."

  new "Port: three index backends + export-canonical to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**This is the big one, and the one that decides whether Stage 1 has a result.**

**Done when**
- \`json\`, \`folder\` and \`sqlite\` each export the canonical representation and all three
  produce \`b36095ef3966ccd1b5f273f914859aa83ff82e2499f695a422e364e8ecf0fc94\`
  on the 20 golden books -- the frozen value in \`spec/golden/expected.sha256\`
- that hash was frozen with positions **on**: index with \`--positions\`

**Traps that actually break the hash**
- sort terms on **UTF-8 bytes**, never the language's native string comparison
- Go: \`SetEscapeHTML(false)\`, or \`<\`, \`>\` and \`&\` come out as \`\\u003c\` etc.
- no trailing newline, no spaces after \`,\` or \`:\`

**References:** SPEC.md §6-§7."

  new "Port: control layer + reconcile to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**Done when**
- invariants I1-I4 pass, including the SIGKILL-at-50%-and-resume test
- \`reconcile\` rebuilds the control files from the datalake contents after a crash between
  the rename and the append

**References:** SPEC.md §2.4; docs/TASKS.md (control-layer invariants)."

  new "Port: query + --metrics-out to $L" "$who" "P2 Ports" "$lab" \
"$PORT_RULE

**Done when**
- for the workloads in \`spec/queries/\`, stdout is **byte-identical** to the Python CLI:
  ids only, one per line, ascending, LF, nothing else
- terms are normalised through the document pipeline before lookup; filtered terms are ignored
- one schema-valid metrics record per run is appended to \`--metrics-out\`

**References:** SPEC.md §1.1 (read it in full -- it is new) and §8."
done

# ========================================================== python remainder

new "Finish the Python CLI: download, split, index, lookup, scan-new, control-step, reconcile" \
"$ANDREA" "P1 Reference" "python,phase-1" \
"\`src/python/cli.py\` currently implements \`version\`, \`query\` and \`export-canonical\`.
The rest are declared and exit 1 naming their owning issue. Wire them up once the datalake
lands (issues for the downloader and the three storages).

**Done when**
- every command in SPEC.md §1 runs against a real workspace
- \`--metrics-out\` appends one schema-valid record per run, wall time from a monotonic clock
- \`version\` **refuses to run** when its SPEC_VERSION disagrees with \`spec/SPEC_VERSION\`
  (SPEC.md line 10 -- no implementation does this yet, in any language)

**References:** SPEC.md §1, §8."

new "Conformance gate: turn on the 27-cell CI matrix" "$ANDREA" "P2 Ports" "python,infra,phase-2" \
"\`tools/run_conformance.sh\` does not exist, which is what keeps the matrix skipped.

**Shape of the runner**
1. serve \`spec/golden/<id>.txt\` over local HTTP at Gutenberg's own path, \`/cache/epub/<id>/pg<id>.txt\`
2. \`engine --workspace W --datalake-layout L --index-backend B --now <fixed ISO8601> download --manifest spec/golden/manifest_20.txt --source-base http://127.0.0.1:PORT\`
3. \`index --all --positions\` (the frozen hash has positions on)
4. \`export-canonical --out canonical.json\`
5. \`sha256sum -c spec/golden/expected.sha256\`

**Also** gate each matrix cell on that language existing, or 18 cells go red the day the
first port lands.

**Done when:** nine identical hashes across 3 languages x 3 backends, all 27 cells green,
tag \`conformance-green\`.

**Blocked by:** the downloader, the three storages and at least one complete CLI."

# ================================================================ benchmarks

new "Choose and document the benchmark machine" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"**Blocking, and the likeliest late failure.** Every number in the same chart must come from one
machine, and the 10 000-book mirror has to get onto it.

**Done when**
- Linux or WSL2 on **ext4** (\`FolderIndex\` silently merges terms differing only in case on
  NTFS and APFS -- the results would be wrong, not just slow)
- CPU, RAM, filesystem type, disk type and OS recorded for the report
- the mirror is present and its size verified
- the cold-cache method is decided, including the fallback without root

**References:** docs/TASKS.md (benchmark protocol)."

new "E1-E5: datalake benchmarks" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"**Done when** every (language x layout) cell is filled for E1 download throughput, E2 lookup
cost p50/p95, E3 incremental, E4 recovery after SIGKILL, E5 storage overhead.

3 repetitions, first discarded as warm-up for all three languages equally; median + IQR,
never mean; teardown outside the timer.

**References:** docs/TASKS.md (experiment matrix, protocol)."

new "E6-E9: index benchmarks" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"**Done when** every (language x backend) cell is filled for E6 indexing speed, E7 query
performance over the four workloads in \`spec/queries/\`, E8 update, E9 memory and disk.

E7 bands were measured on the real corpus: high df 796-998, mid 17-19, rare 2-3.

**References:** docs/TASKS.md (experiment matrix)."

new "E10-E11: scalability sweep 100 / 1 000 / 10 000" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"One unattended session. **Budget for teardown:** \`folder\` at 10 000 creates on the order of a
million files and deleting that tree takes minutes.

**Open decision:** dropping the 10 000 tier to 100 / 1 000 has been recommended and not yet
agreed. If it is dropped, say so in *Threats to validity*.

**References:** docs/TASKS.md."

new "Aggregate results and plot F1-F10" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"**Done when** \`results/summary.csv\` and every figure are committed.

**References:** docs/TASKS.md."

new "Check results against the written predictions" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"Every contradiction is either explained in prose or filed as a bug. A number nobody can
explain is a number that will be asked about.

**Known prediction to check:** measured on 5 golden books, \`json\` and \`sqlite\` index in 0.2 s
and \`folder\` in 6.8 s -- 34x, because it writes one file per term.

**Done when:** each anomaly explained or filed. Then tag \`code-freeze\`."

# ==================================================================== report

new "Report §5: benchmarks and results, with threats to validity" "$MARCELA" "P4 Report" "report,phase-4" \
"**Done when** §5 covers all three languages, the datalake structures, the index structures,
**which layout we chose and why**, and the cross-language trade-offs -- plus a *Threats to
validity* subsection naming: single machine, one filesystem, English-only corpus, 10 000 books
is still modest, the mirror removes network variance, 3 repetitions, SQLite in place of MongoDB,
and the 10 000 tier if it was dropped.

**Blocked by:** the benchmark runs."

new "Report §6: conclusions and future improvements" "$MARCELA" "P4 Report" "report,phase-4" \
"**Done when** it names what Stage 2 inherits: TF-IDF/BM25 come free from the word-level index
with positions, and stemming was deliberately left out because Porter/Snowball ports are not
byte-identical across languages."

new "README: setup and execution" "$ANDREA" "P4 Report" "infra,phase-4" \
"**Done when** a teammate who did not write it runs the whole pipeline from it, clean, in all
three languages.

**Blocked by:** the conformance gate."

new "data/sample/: a small committed dataset" "$ANDREA" "P4 Report" "infra,phase-4" \
"**Done when** the instructor runs the pipeline in one command, with no network and no mirror."

new "Final delivery pass: checklist, git history, PDF upload" "$ANDREA" "P4 Report" "report,phase-4" \
"**Done when**
- every box of the delivery checklist in docs/TASKS.md is ticked -- the cover needs the course
  name **and academic year**, the project title, **full names and student IDs of all four
  members**, the group name and the repo URL
- the git history shows steady progression, not a final-night dump
- the PDF is built and **exactly one member** uploads it to the virtual campus

**Deadline: 1 Oct 2026.**"

echo
echo "Done. Review the new issues, then set milestone due dates."
