#!/usr/bin/env bash
# The conformance gate, one cell.  SPEC.md §7, issue #61.
#
#     bash tools/run_conformance.sh <python|node|go> <json|folder|sqlite> <time|book|hash>
#
# Drives one implementation ONLY through its CLI (SPEC.md §1), end to end,
# against a local mirror of the 20 golden books:
#
#     download --manifest spec/golden/manifest_20.txt   (real downloader, local HTTP)
#     index --all --positions                            (the frozen hash has positions)
#     export-canonical --out canonical.json
#     sha256sum -c spec/golden/expected.sha256
#
# Exit 0 only if the export is byte-identical to the frozen oracle.  Nine
# identical hashes -- 3 languages x 3 backends -- over 3 layouts = 27 cells.
set -euo pipefail

lang=${1:?language: python|node|go}
backend=${2:?backend: json|folder|sqlite}
layout=${3:?layout: time|book|hash}

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
PY=${PYTHON:-$(command -v python3 || command -v python)}

work=$(mktemp -d)
server_pid=""
cleanup() {
  [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

case "$lang" in
  python) engine=("$PY" "$repo/src/python/cli.py") ;;
  node)   engine=(node "$repo/src/node/cli.js") ;;
  go)     (cd src/go && go build -o "$work/engine" ./cmd/engine)
          engine=("$work/engine") ;;
  *)      echo "unknown language: $lang" >&2; exit 2 ;;
esac

# --- local Gutenberg ------------------------------------------------------
"$PY" tools/mirror_server.py --root spec/golden --port-file "$work/port" >/dev/null &
server_pid=$!
for _ in $(seq 100); do [ -s "$work/port" ] && break; sleep 0.1; done
[ -s "$work/port" ] || { echo "mirror server did not start" >&2; exit 1; }
base="http://127.0.0.1:$(tr -d '[:space:]' < "$work/port")"
export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost"

ws="$work/ws"
run() {
  "${engine[@]}" --workspace "$ws" --datalake-layout "$layout" \
                 --index-backend "$backend" --now 2026-01-01T00:00:00Z "$@"
}

echo "== $lang / $backend / $layout"
run download --manifest spec/golden/manifest_20.txt --source-base "$base"
run index --all --positions
run export-canonical --out "$work/canonical.json"

expected=$(cut -d' ' -f1 spec/golden/expected.sha256)
got=$(sha256sum "$work/canonical.json" | cut -d' ' -f1)
echo "expected $expected"
echo "got      $got"
if [ "$got" != "$expected" ]; then
  echo "CONFORMANCE FAILURE: $lang / $backend / $layout" >&2
  exit 1
fi
echo "OK"
