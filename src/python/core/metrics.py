"""
`--metrics-out`: one JSON record per run.  SPEC.md §8.

The CLI measures only what it can measure honestly from the inside: the wall
time of the command, from a monotonic clock, and a few counters.  Peak memory
is deliberately NOT self-reported -- SPEC.md §8 has the runner measure it on
the child process, so every language pays for its runtime in the same way.

Fields describing the experiment rather than the command (experiment id,
repetition, corpus size, machine) are not CLI flags in SPEC.md §1, so the
benchmark runner passes them through the environment:

    BENCH_RUN_ID  BENCH_EXPERIMENT  BENCH_REPETITION
    BENCH_CORPUS_SIZE  BENCH_MACHINE_ID  BENCH_IMPL_VERSION

Anything not set is written as null, never guessed.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["utc_timestamp_ms", "build_record", "append_record"]

REPO = Path(__file__).resolve().parents[3]


def utc_timestamp_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value and value.strip().lstrip("-").isdigit() else None


def _impl_version() -> str:
    if os.environ.get("BENCH_IMPL_VERSION"):
        return os.environ["BENCH_IMPL_VERSION"]
    try:
        rev = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"git:{rev}" if rev else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_record(
    *,
    command: str,
    spec_version: str,
    datalake_layout: str,
    index_backend: str,
    started_at: str,
    wall_time_ms: float,
    positions: bool | None = None,
    workers: int | None = None,
    batch_size: int | None = None,
    aux: dict | None = None,
) -> dict:
    return {
        "run_id": os.environ.get("BENCH_RUN_ID")
        or started_at.replace(":", "-").split(".")[0] + "Z-" + secrets.token_hex(2),
        "spec_version": spec_version,
        "language": "python",
        "impl_version": _impl_version(),
        "experiment": os.environ.get("BENCH_EXPERIMENT") or f"cli_{command}",
        "datalake_layout": datalake_layout,
        "index_backend": index_backend,
        "positions": positions,
        "corpus_size": _env_int("BENCH_CORPUS_SIZE"),
        "workers": workers,
        "batch_size": batch_size,
        "repetition": _env_int("BENCH_REPETITION"),
        "metric": "wall_time",
        "value": round(wall_time_ms, 3),
        "unit": "ms",
        "aux": dict(aux or {}),
        "machine_id": os.environ.get("BENCH_MACHINE_ID") or socket.gethostname(),
        "started_at": started_at,
    }


def append_record(path: str | Path, record: dict) -> None:
    """One line, compact separators, LF -- appended, never rewritten."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(target, "ab") as fh:
        fh.write(line.encode("utf-8"))
