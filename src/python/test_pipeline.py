"""
End-to-end tests of the pipeline commands: download, index, lookup, scan-new,
--metrics-out.  Issue #60.

A local HTTP server plays Gutenberg, serving the golden books at Gutenberg's
own path (`/cache/epub/<id>/pg<id>.txt`, SPEC.md §2.1), so the CLI runs
exactly as it will against the benchmark mirror, with no network.

The first test is the one that matters: the whole Python pipeline, driven
only through the CLI, reproduces the frozen canonical hash.  That is the
Python column of the conformance gate (#61) in miniature.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import cli  # noqa: E402  (sets up the import paths for the rest)

GOLDEN = REPO / "spec" / "golden"
GOLDEN_IDS = [int(x) for x in (GOLDEN / "manifest_20.txt").read_text().split()]
EXPECTED = (GOLDEN / "expected.sha256").read_text().split()[0]
NO_MARKERS_ID = 999999
FEW = sorted(GOLDEN_IDS)[:3]


class _Mirror(BaseHTTPRequestHandler):
    requests_seen: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).requests_seen.append(self.path)
        parts = self.path.strip("/").split("/")  # cache/epub/<id>/pg<id>.txt
        body = None
        if len(parts) == 4 and parts[:2] == ["cache", "epub"]:
            book_id = parts[2]
            if book_id == str(NO_MARKERS_ID):
                body = b"Title: No markers here\n\nJust some text.\n"
            elif (GOLDEN / f"{book_id}.txt").exists():
                body = (GOLDEN / f"{book_id}.txt").read_bytes()
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def mirror():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Mirror)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # A proxy in the environment must not intercept the local mirror.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def run(ws: Path, *argv: str, layout: str = "hash", backend: str = "json") -> int:
    return cli.main(["--workspace", str(ws), "--datalake-layout", layout,
                     "--index-backend", backend, *argv])


def download(ws, mirror, ids, layout="hash", *extra):
    manifest = ws / "manifest.txt"
    ws.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(f"{i}\n" for i in ids))
    return run(ws, "--now", "2026-01-01T07:30:00Z", "download", "--manifest", str(manifest),
               "--source-base", mirror, *extra, layout=layout)


def canonical(ws, backend="json", layout="hash") -> bytes:
    out = ws / f"canonical-{backend}.json"
    assert run(ws, "export-canonical", "--out", str(out), layout=layout, backend=backend) == 0
    return out.read_bytes()


# ----------------------------------------------------- the one that matters


def test_cli_pipeline_reproduces_the_frozen_hash(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, GOLDEN_IDS) == 0
    assert run(ws, "index", "--all", "--positions") == 0
    assert hashlib.sha256(canonical(ws)).hexdigest() == EXPECTED


# ------------------------------------------------ layouts and backends agree


@pytest.mark.parametrize("layout", ["time", "book"])
def test_every_layout_feeds_the_same_index(tmp_path, mirror, layout):
    ref, other = tmp_path / "ref", tmp_path / layout
    assert download(ref, mirror, FEW, "hash") == 0
    assert run(ref, "index", "--all", "--positions") == 0
    assert download(other, mirror, FEW, layout) == 0
    assert run(other, "index", "--all", "--positions", layout=layout) == 0
    assert canonical(other, layout=layout) == canonical(ref)


def test_every_backend_exports_the_same_bytes(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW) == 0
    exports = []
    for backend in ("json", "folder", "sqlite"):
        # each backend keeps its own record of what it has indexed
        (ws / "control" / "indexed_books.txt").unlink(missing_ok=True)
        assert run(ws, "index", "--all", "--positions", backend=backend) == 0
        exports.append(canonical(ws, backend))
    assert exports[0] == exports[1] == exports[2]


# ---------------------------------------------------------------- download


def test_download_is_idempotent(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW) == 0
    _Mirror.requests_seen.clear()
    assert download(ws, mirror, FEW) == 0
    assert _Mirror.requests_seen == []  # I4: zero requests, zero writes
    lines = (ws / "control" / "downloaded_books.txt").read_text().split()
    assert sorted(map(int, lines)) == FEW  # I1: no duplicates


def test_parallel_download_writes_the_same_datalake(tmp_path, mirror):
    serial, parallel = tmp_path / "s", tmp_path / "p"
    assert download(serial, mirror, GOLDEN_IDS[:8]) == 0
    assert download(parallel, mirror, GOLDEN_IDS[:8], "hash", "--workers", "4") == 0
    for i in GOLDEN_IDS[:8]:
        assert run(serial, "lookup", "--book-id", str(i)) == 0
    files = lambda ws: sorted(p.relative_to(ws).as_posix()  # noqa: E731
                              for p in (ws / "datalake").rglob("*.txt"))
    assert files(serial) == files(parallel)
    for rel in files(serial):
        assert (serial / rel).read_bytes() == (parallel / rel).read_bytes()


def test_missing_book_exits_3_and_is_recorded(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, [424242]) == 3
    line = (ws / "control" / "failed_books.txt").read_text().strip().split("\t")
    assert line[:2] == ["424242", "NOT_FOUND"]
    assert not (ws / "datalake").exists() or not any((ws / "datalake").rglob("*.txt"))


def test_book_without_markers_exits_3_and_writes_nothing(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, [NO_MARKERS_ID]) == 3
    line = (ws / "control" / "failed_books.txt").read_text().strip().split("\t")
    assert line[:2] == [str(NO_MARKERS_ID), "NO_MARKERS"]
    assert not (ws / "control" / "downloaded_books.txt").exists()


def test_time_layout_uses_now(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW[:1], "time") == 0
    assert (ws / "datalake" / "20260101" / "07" / f"{FEW[0]}.body.txt").exists()


def test_unreachable_source_exits_1(tmp_path, monkeypatch):
    import datalake.downloader as dl
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)  # skip the backoff
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "m.txt").write_text(f"{FEW[0]}\n")
    code = run(ws, "download", "--manifest", str(ws / "m.txt"),
               "--source-base", "http://127.0.0.1:9")
    assert code == 1
    assert "DOWNLOAD_ERROR" in (ws / "control" / "failed_books.txt").read_text()


# ------------------------------------------------------- lookup / scan-new


def test_lookup_prints_relative_posix_paths(tmp_path, mirror, capsys):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW[:1]) == 0
    capsys.readouterr()
    assert run(ws, "lookup", "--book-id", str(FEW[0])) == 0
    header, body = capsys.readouterr().out.rstrip("\n").split("\t")
    assert body.endswith(f"{FEW[0]}.body.txt") and "\\" not in body
    assert (ws / header).exists() and (ws / body).exists()


def test_lookup_missing_exits_3(tmp_path, capsys):
    assert run(tmp_path, "lookup", "--book-id", "123") == 3
    assert capsys.readouterr().out == ""


def test_scan_new_lists_downloaded_but_not_indexed(tmp_path, mirror, capsys):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW) == 0
    capsys.readouterr()
    assert run(ws, "scan-new") == 0
    assert capsys.readouterr().out == "".join(f"{i}\n" for i in FEW)
    assert run(ws, "index", "--all") == 0
    capsys.readouterr()
    assert run(ws, "scan-new") == 0
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------- index


def test_index_all_twice_indexes_nothing_new(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW) == 0
    assert run(ws, "index", "--all", "--positions") == 0
    first = canonical(ws)
    assert run(ws, "index", "--all", "--positions") == 0
    assert canonical(ws) == first
    assert len((ws / "control" / "indexed_books.txt").read_text().split()) == len(FEW)


def test_positions_cannot_change_on_an_existing_index(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW) == 0
    assert run(ws, "index", "--all", "--positions") == 0
    assert run(ws, "index", "--all") == 2


def test_export_without_positions_uses_pairs(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert download(ws, mirror, FEW[:1]) == 0
    assert run(ws, "index", "--all") == 0
    data = json.loads(canonical(ws))
    first = next(iter(data.values()))
    assert all(len(p) == 2 for p in first["postings"])  # [id, tf], SPEC.md §7


def test_index_unknown_book_exits_3(tmp_path):
    assert run(tmp_path, "index", "--book-id", "5") == 3


# ----------------------------------------------------------------- metrics


def test_metrics_out_appends_one_record_per_run(tmp_path, mirror, monkeypatch):
    monkeypatch.setenv("BENCH_EXPERIMENT", "E6_index_build")
    monkeypatch.setenv("BENCH_REPETITION", "2")
    ws, out = tmp_path / "ws", tmp_path / "metrics.jsonl"
    assert download(ws, mirror, FEW) == 0
    assert run(ws, "--metrics-out", str(out), "index", "--all", "--positions") == 0
    assert run(ws, "--metrics-out", str(out), "scan-new") == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == 2
    rec = records[0]
    assert rec["experiment"] == "E6_index_build" and rec["repetition"] == 2
    assert rec["language"] == "python" and rec["spec_version"] == cli.SUPPORTED_SPEC_VERSION
    assert rec["metric"] == "wall_time" and rec["unit"] == "ms" and rec["value"] > 0
    assert rec["positions"] is True and rec["batch_size"] == 500
    assert rec["aux"]["docs_processed"] == len(FEW)
    assert "peak_rss_bytes" not in rec["aux"]  # measured by the runner, SPEC.md §8


def test_version_writes_no_metrics(tmp_path):
    out = tmp_path / "m.jsonl"
    assert run(tmp_path, "--metrics-out", str(out), "version") == 0
    assert not out.exists()
