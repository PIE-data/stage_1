"""
The Python --metrics-out records against the shared schema.  SPEC.md §8.

spec/schemas/metrics.schema.json is shared by the three implementations; the
Node suite validates its records against it (src/node/metrics.schema.test.js),
this file does the same for the reference implementation, so the schema can
never again be stricter than what Python actually writes.

Needs `jsonschema`; skipped where it is not installed (CI installs it).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / "src" / "python" / "cli.py"
SCHEMA = json.loads((REPO / "spec" / "schemas" / "metrics.schema.json").read_text("utf-8"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import build_record  # noqa: E402


def _validate(record: dict) -> None:
    jsonschema.validate(record, SCHEMA, format_checker=jsonschema.FormatChecker())


@pytest.mark.parametrize("fields", [
    {},                                                    # e.g. scan-new: all optional fields null
    {"positions": True, "batch_size": 500},                # index
    {"workers": 8},                                        # download
])
def test_build_record_satisfies_the_schema(fields):
    _validate(build_record(command="x", spec_version="1.1.5", datalake_layout="hash",
                           index_backend="json", started_at="2026-09-30T10:00:00.000Z",
                           wall_time_ms=12.5, aux={"exit_code": 0}, **fields))


def test_a_real_cli_record_satisfies_the_schema(tmp_path):
    """End to end: what `scan-new --metrics-out` really appends."""
    out = tmp_path / "metrics.jsonl"
    proc = subprocess.run(
        [sys.executable, str(CLI), "--workspace", str(tmp_path / "ws"),
         "--datalake-layout", "hash", "--metrics-out", str(out), "scan-new"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    records = [json.loads(line) for line in out.read_text("utf-8").splitlines()]
    assert len(records) == 1
    _validate(records[0])


def test_the_schema_still_rejects_bad_records():
    good = build_record(command="x", spec_version="1.1.5", datalake_layout="hash",
                        index_backend="json", started_at="2026-09-30T10:00:00.000Z",
                        wall_time_ms=1.0)
    for field, value in [("value", -1), ("workers", 0), ("positions", "true"),
                         ("unit", "s"), ("language", "java")]:
        bad = dict(good, **{field: value})
        with pytest.raises(jsonschema.ValidationError):
            _validate(bad)
