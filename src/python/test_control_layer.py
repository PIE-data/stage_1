"""
The control layer: control-step, reconcile, run.lock, and the four invariants.
Issue #5.  docs/STAGE1.md Part 5, docs/TASKS.md.

    I1  no duplicates        an id appears at most once per control file
    I2  no loss              every downloaded id has readable artifacts
    I3  crash safety         SIGKILL mid-run + restart == the uninterrupted run
    I4  idempotency          re-running on a complete corpus writes nothing

I3 is tested for real: the CLI runs as a child process against a deliberately
slow local mirror and is killed with SIGKILL (TerminateProcess on Windows)
half-way through, for each of the three datalake layouts.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import cli  # noqa: E402
from control_layer import WorkspaceLock  # noqa: E402

GOLDEN = REPO / "spec" / "golden"
IDS = sorted(int(x) for x in (GOLDEN / "manifest_20.txt").read_text().split())[:10]
NOW = "2026-01-01T07:30:00Z"


class _Mirror(BaseHTTPRequestHandler):
    seen: list[str] = []
    delay = 0.0

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.path)
        time.sleep(type(self).delay)
        parts = self.path.strip("/").split("/")
        path = GOLDEN / f"{parts[2]}.txt" if len(parts) == 4 else None
        if path is None or not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def mirror():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Mirror)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Mirror.delay = 0.0


def argv(ws: Path, layout: str, *rest: str) -> list[str]:
    return ["--workspace", str(ws), "--datalake-layout", layout,
            "--index-backend", "json", "--now", NOW, *rest]


def step(ws, mirror, ids=IDS, iterations=100, layout="hash") -> int:
    manifest = ws / "manifest.txt"
    ws.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(f"{i}\n" for i in ids))
    return cli.main(argv(ws, layout, "control-step", "--iterations", str(iterations),
                         "--manifest", str(manifest), "--source-base", mirror))


def lines(ws: Path, name: str) -> list[str]:
    path = ws / "control" / name
    return path.read_text().split() if path.exists() else []


def export(ws: Path, layout="hash") -> bytes:
    out = ws.parent / f"{ws.name}-canonical.json"
    assert cli.main(argv(ws, layout, "export-canonical", "--out", str(out))) == 0
    return out.read_bytes()


def snapshot(ws: Path) -> dict[str, bytes]:
    """Every file of the workspace except the lock, by relative path."""
    return {p.relative_to(ws).as_posix(): p.read_bytes()
            for p in sorted(ws.rglob("*")) if p.is_file() and p.name not in
            ("run.lock", "manifest.txt")}


# ------------------------------------------------------------ control-step


def test_control_step_takes_the_corpus_to_completion(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror) == 0
    assert sorted(map(int, lines(ws, "downloaded_books.txt"))) == IDS
    assert sorted(map(int, lines(ws, "indexed_books.txt"))) == IDS

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "m.txt").write_text("".join(f"{i}\n" for i in IDS))
    assert cli.main(argv(ref, "hash", "download", "--manifest", str(ref / "m.txt"),
                         "--source-base", mirror)) == 0
    assert cli.main(argv(ref, "hash", "index", "--all", "--positions")) == 0
    assert export(ws) == export(ref)


def test_pending_books_are_indexed_before_anything_is_downloaded(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror, iterations=3) == 0  # download, index, download
    assert len(lines(ws, "downloaded_books.txt")) == 2
    assert len(lines(ws, "indexed_books.txt")) == 1
    _Mirror.seen.clear()
    assert step(ws, mirror, iterations=1) == 0  # the pending book, no request
    assert _Mirror.seen == []
    assert len(lines(ws, "indexed_books.txt")) == 2


def test_failed_books_are_not_retried(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror, ids=[424242, IDS[0]]) == 0
    assert lines(ws, "failed_books.txt")[:2] == ["424242", "NOT_FOUND"]
    _Mirror.seen.clear()
    assert step(ws, mirror, ids=[424242, IDS[0]]) == 0
    assert _Mirror.seen == []


def test_I4_a_complete_corpus_performs_zero_writes(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror) == 0
    before = snapshot(ws)
    mtimes = {p: p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()
              and p.name not in ("run.lock", "manifest.txt")}
    _Mirror.seen.clear()
    assert step(ws, mirror) == 0
    assert _Mirror.seen == []
    assert snapshot(ws) == before
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes


# --------------------------------------------------------------- run.lock


def test_a_second_writer_gets_exit_4(tmp_path, mirror):
    ws = tmp_path / "ws"
    ws.mkdir()
    with WorkspaceLock(ws):
        assert step(ws, mirror) == 4
        # readers are not blocked
        assert cli.main(argv(ws, "hash", "scan-new")) == 0
    assert step(ws, mirror) == 0  # released on exit


# -------------------------------------------------------------- reconcile


def test_reconcile_repairs_the_control_files(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror, ids=IDS[:4]) == 0
    control = ws / "control"
    kept = lines(ws, "downloaded_books.txt")
    # crash between rename and append: the last book is on disk, not recorded;
    # plus a duplicate, a ghost id with no artifacts, and a half-written file
    (control / "downloaded_books.txt").write_text(
        "\n".join(kept[:-1] + [kept[0], "777777"]) + "\n")
    (ws / "datalake" / "stray.body.txt.part").write_text("half")
    assert cli.main(argv(ws, "hash", "reconcile")) == 0
    assert lines(ws, "downloaded_books.txt") == sorted(kept, key=int)  # I1, I2
    assert not list(ws.rglob("*.part"))
    assert set(lines(ws, "indexed_books.txt")) <= set(kept)


def test_reconcile_on_a_healthy_workspace_changes_nothing_that_matters(tmp_path, mirror):
    ws = tmp_path / "ws"
    assert step(ws, mirror, ids=IDS[:3]) == 0
    downloaded, indexed = lines(ws, "downloaded_books.txt"), lines(ws, "indexed_books.txt")
    assert cli.main(argv(ws, "hash", "reconcile")) == 0
    assert sorted(lines(ws, "downloaded_books.txt"), key=int) == sorted(downloaded, key=int)
    assert sorted(lines(ws, "indexed_books.txt"), key=int) == sorted(indexed, key=int)


# ------------------------------------------------------ I3: SIGKILL + resume


@pytest.mark.parametrize("layout", ["time", "book", "hash"])
def test_I3_sigkill_mid_run_then_resume_converges(tmp_path, mirror, layout):
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("".join(f"{i}\n" for i in IDS))

    # the reference: one uninterrupted run
    ref = tmp_path / "ref"
    ref.mkdir()
    assert cli.main(argv(ref, layout, "control-step", "--iterations", "100",
                         "--manifest", str(manifest), "--source-base", mirror)) == 0

    # the victim: same run in a child process, killed half-way
    ws = tmp_path / "ws"
    ws.mkdir()
    _Mirror.delay = 0.15  # slow enough to land the kill mid-run
    env = dict(os.environ, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    child = subprocess.Popen(
        [sys.executable, str(HERE / "cli.py"), *argv(ws, layout, "control-step",
         "--iterations", "100", "--manifest", str(manifest), "--source-base", mirror)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while len(lines(ws, "downloaded_books.txt")) < len(IDS) // 2:
        assert child.poll() is None, "the run finished before it could be killed"
        assert time.monotonic() < deadline
        time.sleep(0.02)
    child.kill()  # SIGKILL on POSIX, TerminateProcess on Windows
    child.wait()
    _Mirror.delay = 0.0
    assert len(lines(ws, "downloaded_books.txt")) < len(IDS)  # really interrupted

    # resume: repair, then carry on
    assert cli.main(argv(ws, layout, "reconcile")) == 0
    assert cli.main(argv(ws, layout, "control-step", "--iterations", "100",
                         "--manifest", str(manifest), "--source-base", mirror)) == 0

    for name in ("downloaded_books.txt", "indexed_books.txt"):
        got = lines(ws, name)
        assert len(got) == len(set(got)), f"I1: duplicates in {name}"
        assert sorted(map(int, got)) == IDS
    for book_id in IDS:  # I2
        assert cli.main(argv(ws, layout, "lookup", "--book-id", str(book_id))) == 0
    assert export(ws, layout) == export(ref, layout)  # I3
    datalake = lambda root: {k: v for k, v in snapshot(root).items()  # noqa: E731
                             if k.startswith("datalake/")}
    assert datalake(ws) == datalake(ref)
