#!/usr/bin/env bash
# Scaffold the stage_1 repository.  Run once, from the repo root.
#
#   bash scaffold.sh
#
# Creates the directory tree, .gitignore, CODEOWNERS and placeholder files.
# Existing files are never overwritten.

set -euo pipefail

[[ -d .git ]] || { echo "Run this from the repo root (no .git here)." >&2; exit 1; }

keep() { mkdir -p "$1" && [[ -e "$1/.gitkeep" ]] || touch "$1/.gitkeep"; }
new()  { [[ -e "$1" ]] && { echo "  skip   $1"; return 1; } || { echo "  create $1"; return 0; }; }

echo "== directories =="
for d in docs spec/corpus spec/queries spec/golden spec/schemas \
         src/python/datalake src/python/datamart src/python/core \
         src/node/datalake  src/node/datamart  src/node/core \
         src/go/datalake    src/go/datamart    src/go/core   src/go/cmd/engine \
         src/benchmark tools infra/mirror data/sample results report/figures \
         .github/workflows; do
  keep "$d"; echo "  $d"
done

# The mirror and the runtime workspace are never tracked.
rm -f infra/mirror/.gitkeep

echo
echo "== files =="

if new .gitignore; then cat > .gitignore <<'EOF'
# --- corpus and runtime data: NEVER commit -----------------------------
# The mirror is ~1 GB and the datalake/datamarts are regenerable artifacts.
infra/mirror/
workspace/
/data/*
!/data/sample/

# --- generated datamarts -----------------------------------------------
*.db
*.db-wal
*.db-shm
inverted_index.json
inverted_index/

# --- language toolchains ------------------------------------------------
__pycache__/
*.py[cod]
.venv/
venv/
.mypy_cache/
.pytest_cache/
.ruff_cache/
node_modules/
package-lock.json
/src/go/engine
/src/go/**/engine

# --- LaTeX build artifacts (report.pdf is built, not edited) -----------
report/*.aux
report/*.log
report/*.out
report/*.toc
report/*.fls
report/*.fdb_latexmk
report/*.synctex.gz

# --- editors / OS -------------------------------------------------------
.DS_Store
Thumbs.db
.idea/
.vscode/
*.swp
EOF
fi

if new CODEOWNERS; then cat > CODEOWNERS <<'EOF'
# One owner per area. Fill in the GitHub handles.
# A PR touching these paths needs that owner's review.

/docs/            @andreapatruno1
/spec/            @andreapatruno1
/src/python/      @
/src/node/        @
/src/go/          @
/src/benchmark/   @
/report/          @
EOF
fi

if new README.md; then cat > README.md <<'EOF'
# Stage 1 — Data Layer

Search engine data layer: datalake, datamarts and control layer, implemented in
Python, Node and Go and benchmarked against each other.

Big Data · Grado en Ciencia e Ingeniería de Datos · Universidad de Las Palmas de Gran Canaria
Group: **PIE data**

## Documentation

| | |
|---|---|
| `docs/STAGE1.md` | What is built, section by section against the brief |
| `docs/SPEC.md` | **Binding** cross-language contract — if the code disagrees, the code is wrong |
| `docs/TASKS.md` | Work breakdown |

## Setup

TODO — issue #49. Must be verified on a clean machine by someone who did not write it.

## Running

TODO

## Benchmarks

TODO
EOF
fi

if new spec/SPEC_VERSION; then echo "1.0.0" > spec/SPEC_VERSION; fi

if new spec/stopwords_en.txt; then cat > spec/stopwords_en.txt <<'EOF'
# English stop words for the Stage 1 inverted index.
# One lowercase term per line. Lines starting with # are comments.
# Committed deliberately: library lists differ between languages, which would
# make the three implementations produce different indexes.
# TODO (issue #9): complete this list.
EOF
fi

if new .github/workflows/ci.yml; then cat > .github/workflows/ci.yml <<'EOF'
name: CI
on:
  pull_request:
  push:
    branches: [main]

jobs:
  conformance:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        language: [python, node, go]
        backend:  [json, folder, sqlite]
        layout:   [time, book, hash]
    steps:
      - uses: actions/checkout@v4
      # TODO (issue #7): set up each toolchain, build, then assert that
      # `export-canonical` over spec/golden/ matches spec/golden/expected.sha256.
      - name: Placeholder
        run: |
          echo "${{ matrix.language }} / ${{ matrix.backend }} / ${{ matrix.layout }}"
          exit 1
EOF
fi

echo
echo "== check: is the mirror ignored? =="
mkdir -p infra/mirror && touch infra/mirror/_probe.txt
if git check-ignore -q infra/mirror/_probe.txt; then
  echo "  OK — infra/mirror/ is ignored"
else
  echo "  WARNING — infra/mirror/ is NOT ignored. Fix .gitignore before downloading." >&2
fi
rm -f infra/mirror/_probe.txt

echo
echo "Next:"
echo "  git add -A && git commit -m 'chore: scaffold repository structure'"
echo "  git push"
echo "  python tools/build_mirror.py --target 2000"
