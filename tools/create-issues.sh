#!/usr/bin/env bash
# Open the issues that phases 1-4 need.  Run once, from the repository root:
#
#     bash tools/create-issues.sh
#
# Requires the GitHub CLI, authenticated:  gh auth status
#
# Issues are deliberately short: context, acceptance criteria, references.
# The specification is in docs/SPEC.md and is not duplicated here -- an issue
# that restates the spec is a second copy of the spec, and the two drift.
set -euo pipefail

command -v gh >/dev/null || { echo "gh not found: https://cli.github.com"; exit 1; }

ANDREA=andreapatruno1
IVAN=ivanc25
MANUEL=maneloco
MARCELA=marcelakuczynska

# Labels used below; harmless if they already exist.
for l in node go bench report spec-question phase-1 phase-2 phase-3 phase-4; do
  gh label create "$l" --force >/dev/null 2>&1 || true
done

new () {  # new <title> <assignee> <milestone> <labels> <body>
  gh issue create --title "$1" --assignee "$2" --milestone "$3" --label "$4" --body "$5"
}

# ---------------------------------------------------------------- unblockers

new "Node CLI skeleton: version command" "$IVAN" "P2 Ports" "node,phase-2" \
"Gives CI something to run and the port somewhere to live. Nothing else.

**Done when**
- \`src/node\` has a \`package.json\` and an entry point
- the CLI prints the contents of \`spec/SPEC_VERSION\` for the \`version\` command and exits 0
- \`npm test --prefix src/node\` runs (even with a single trivial test) so the CI job switches on

**References:** SPEC.md §1 (CLI contract)."

new "Go CLI skeleton: version command" "$MANUEL" "P2 Ports" "go,phase-2" \
"Gives CI something to run and the port somewhere to live. Nothing else.

**Done when**
- \`src/go\` has a \`go.mod\` and \`cmd/engine\`
- the CLI prints the contents of \`spec/SPEC_VERSION\` for the \`version\` command and exits 0
- \`go test ./...\` runs so the CI job switches on

**References:** SPEC.md §1 (CLI contract)."

# --------------------------------------------------------------------- ports

PORT_RULE="**Write it from SPEC.md §3, not from \`src/python/core/tokenizer.py\`.**
Reading the reference makes the two implementations agree by accident: the
conformance hash then passes even where the spec is ambiguous or wrong, which
is precisely what it exists to catch. If the spec is unclear, open an issue
labelled \`spec-question\` -- those answers become §4 of the report."

new "Port: tokenizer to Node" "$IVAN" "P2 Ports" "node,phase-2" \
"$PORT_RULE

**Done when**
- for every book in \`spec/golden/manifest_20.txt\`, the implementation reproduces
  \`n_tokens_raw\`, \`n_tokens_kept\` and \`sha256_tokens\` from \`spec/golden/tokens_20.jsonl\` exactly
- a unit test asserts that, and runs in CI

**Traps (all in SPEC.md, listed because they are what actually breaks the hash)**
- iterate code points with \`for (const ch of s)\`, never \`s[i]\`: JS strings are UTF-16
  and astral characters split in half (three golden books contain them on purpose)
- \`toLowerCase()\`, never \`toLocaleLowerCase()\`
- positions are assigned BEFORE filtering

**References:** SPEC.md §3.1-§3.4."

new "Port: tokenizer to Go" "$MANUEL" "P2 Ports" "go,phase-2" \
"$PORT_RULE

**Done when**
- for every book in \`spec/golden/manifest_20.txt\`, the implementation reproduces
  \`n_tokens_raw\`, \`n_tokens_kept\` and \`sha256_tokens\` from \`spec/golden/tokens_20.jsonl\` exactly
- a unit test asserts that, and runs in CI

**Traps**
- NFKC comes from \`golang.org/x/text/unicode/norm\`, not the standard library
- range over a string yields runes: that is correct here, \`s[i]\` yields bytes and is not
- positions are assigned BEFORE filtering

**References:** SPEC.md §3.1-§3.4."

# ------------------------------------------------------------ critical path

new "Three inverted-index backends: json, folder, sqlite" "$ANDREA" "P1 Reference" "python,phase-1" \
"The three structures the brief requires us to compare. Same input, same
postings, three storage strategies: one big file, one file per term, an
embedded B-tree.

**Done when**
- \`index --all\` works for all three backends
- the same corpus produces the same postings in all three (a test asserts it)
- \`folder\` percent-encodes every code point outside \`[a-z0-9]\` -- raw terms as
  filenames collide on case-insensitive filesystems and break on reserved names
- \`sqlite\` uses \`terms\` + \`postings WITHOUT ROWID\`, batched in one transaction

**References:** SPEC.md §6.1-§6.3."

new "export-canonical and freeze spec/golden/expected.sha256" "$ANDREA" "P1 Reference" "python,phase-1,spec" \
"The moment the project becomes verifiable. Until this exists, no port can be
proven equivalent and §5 of the report rests on an unchecked claim.

**Done when**
- \`export-canonical\` emits byte-identical JSON for the 20 golden books
- the three backends produce ONE identical hash
- that hash is committed to \`spec/golden/expected.sha256\` and frozen
- CI's conformance matrix switches on (it keys off that file existing)

**Blocks:** both ports' index work. Highest priority on the board.

**References:** SPEC.md §7."

new "query --terms --mode and|or" "$ANDREA" "P1 Reference" "python,phase-1" \
"Brief §1 defers querying to Stage 2, but §7 grades it now. Resolving that
contradiction protects 30% of the mark.

**Done when**
- \`query --terms \"a b\" --mode and|or\` returns a deterministic, sorted id list
- the three index backends return identical lists for the same query
- \`--limit\` works

**References:** SPEC.md §1, §6."

# ------------------------------------------------- benchmarks and the report

new "Benchmark runner" "$MARCELA" "P3 Benchmarks" "bench,phase-3" \
"Build it now against a stub: it does not need the ports to exist, and it is
what turns a working pipeline into the numbers §5 is made of.

**Done when**
- repetitions configurable, first run discarded as warm-up, teardown outside the timer
- peak memory measured by the runner on the child process, never self-reported
- preflight aborts when disk or inodes are short
- a dry run over \`manifest_100\` completes

**Protocol (non-negotiable -- without these the numbers are noise)**
- one machine for any set of numbers that appears in the same chart; record CPU, RAM,
  filesystem, disk, OS
- Linux or WSL2 on ext4: the \`folder\` backend silently merges terms differing only in
  case on NTFS and APFS
- report median and IQR, never mean and standard deviation: these distributions are skewed
- all fetches hit the local mirror

**Decide early:** which machine runs the benchmarks, and how the mirror gets there.

**References:** docs/TASKS.md phase 3."

new "Report: cover page, Introduction, Architecture, Design decisions" "$MARCELA" "P4 Report" "report,phase-4" \
"These four sections need no benchmark results. Write them now, while the
decisions are fresh -- half the mark is report and analysis.

**Done when**
- \`report/main.tex\` builds
- cover: course name AND academic year, title, full names AND student IDs of all four,
  group name, repository URL
- §2 Introduction and objectives, scoped to Stage 1
- §3 System architecture: datalake, datamart AND control layer
- §4 Design decisions, each one argued: three languages, sqlite instead of MongoDB,
  English-only corpus, no stemming, word-level index with positions, local mirror

**References:** docs/TASKS.md phase 4, the decision table in docs/STAGE1.md."

# -------------------------------------------------------------- house-keeping

new "spec-question: the datalake layout named 'hash' is not a hash" "$ANDREA" "P1 Reference" "spec-question,spec" \
"SPEC.md §1 names the flag value \`hash\`, but §4 defines its path rule as the
digit prefix \`<id6[0:2]>/<id6[2:4]>\` -- no hash function anywhere. The name
will mislead whoever implements it, and §4 of the report has to defend
'digit prefix, deliberately not a hash'.

No code uses the value yet, so renaming it to \`batch\` costs three files today
and far more later. Alternative: keep \`hash\` and document the contradiction.

**Done when** all four have agreed, SPEC.md and ci.yml match, and the spec tag is
moved if the value changed."

new "Verify git email attribution for all four members" "$ANDREA" "P0 Foundations" "infra" \
"Graded: brief §6.2 asks for a git history that shows the work of the group.
A commit made with an email not linked to its GitHub account shows no profile
and does not enter the contributor graph -- the examiner sees fewer authors
than there are.

**Done when** the contributor graph lists all four. Fix by adding the address in
GitHub Settings -> Emails; no history rewrite needed."

echo
echo "Done. Check the board: https://github.com/PIE-data/stage_1/issues"
