"""
Tests for the query command.  Issue #21 / task T24.

The acceptance criterion is one sentence long and it is the whole point of the
task: *the same id list from all three backends*.  So every behavioural test
below runs three times, once per backend, and a final test asserts the three
outputs are byte-identical.  If that ever fails, the comparison in report §5
is comparing three programs that answer differently.

The CLI is also exercised as a subprocess, because the format of stdout is
part of the contract the Node and Go ports must reproduce, and a function call
would not catch a stray print.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _sub in ("core", "datalake", "datamart"):
    sys.path.insert(0, str(HERE / _sub))
sys.path.insert(0, str(HERE))

from cli import check_spec_version, query_terms, run_query  # noqa: E402
from index_base import open_index  # noqa: E402

BACKENDS = ["json", "folder", "sqlite"]
CLI = HERE / "cli.py"

# Three tiny books.  Positions are pre-filter ordinals (SPEC.md §3.3), which is
# why they are not 0..n -- the gaps are where stop words were dropped.
BOOKS = {
    11: [("whale", 0), ("ship", 2), ("whale", 7)],
    22: [("whale", 1), ("sea", 4)],
    33: [("ship", 0), ("sea", 3), ("ship", 8)],
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """One workspace holding all three indexes: they use different paths."""
    for backend in BACKENDS:
        index = open_index(backend, tmp_path)
        with index.batch() as batched:
            for book_id, tokens in BOOKS.items():
                batched.add_book(book_id, tokens)
        index.close()
    return tmp_path


def ids(workspace: Path, backend: str, terms, mode: str, limit=None):
    index = open_index(backend, workspace)
    try:
        return run_query(index, terms, mode, limit)
    finally:
        index.close()


# ----------------------------------------------------------- term handling


def test_query_terms_normalises_like_a_document():
    # Uppercase and accents must fold, or a query never matches the index.
    assert query_terms("WHALE Café") == ["whale", "cafe"]


def test_query_terms_drops_stop_words_and_noise():
    # "the" is a stop word, "a" is too short, "1234" is all digits.
    assert query_terms("the a 1234 whale") == ["whale"]


def test_query_terms_removes_duplicates_but_keeps_order():
    assert query_terms("whale ship whale") == ["whale", "ship"]


# ------------------------------------------------------------- the search


@pytest.mark.parametrize("backend", BACKENDS)
def test_and_is_the_intersection(workspace, backend):
    assert ids(workspace, backend, ["whale", "ship"], "and") == [11]


@pytest.mark.parametrize("backend", BACKENDS)
def test_or_is_the_union(workspace, backend):
    assert ids(workspace, backend, ["whale", "ship"], "or") == [11, 22, 33]


@pytest.mark.parametrize("backend", BACKENDS)
def test_results_are_ascending_by_id(workspace, backend):
    got = ids(workspace, backend, ["sea", "ship"], "or")
    assert got == sorted(got) == [11, 22, 33]


@pytest.mark.parametrize("backend", BACKENDS)
def test_single_term(workspace, backend):
    assert ids(workspace, backend, ["sea"], "and") == [22, 33]
    assert ids(workspace, backend, ["sea"], "or") == [22, 33]


@pytest.mark.parametrize("backend", BACKENDS)
def test_absent_term(workspace, backend):
    assert ids(workspace, backend, ["kangaroo"], "or") == []
    assert ids(workspace, backend, ["whale", "kangaroo"], "and") == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_empty_term_list_matches_nothing_in_both_modes(workspace, backend):
    # A stop-word-only query must not degenerate into "the whole corpus".
    assert ids(workspace, backend, [], "and") == []
    assert ids(workspace, backend, [], "or") == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_limit_truncates_after_sorting(workspace, backend):
    assert ids(workspace, backend, ["whale", "ship"], "or", limit=2) == [11, 22]
    assert ids(workspace, backend, ["whale", "ship"], "or", limit=0) == []


# -------------------------------------------------- the three agree exactly


def run_cli(workspace: Path, backend: str, *extra: str):
    return subprocess.run(
        [sys.executable, str(CLI), "--workspace", str(workspace),
         "--index-backend", backend, "query", *extra],
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("mode", ["and", "or"])
def test_three_backends_produce_identical_stdout(workspace, mode):
    outs = {}
    for backend in BACKENDS:
        proc = run_cli(workspace, backend, "--terms", "whale ship sea", "--mode", mode)
        assert proc.returncode == 0, proc.stderr
        outs[backend] = proc.stdout
    assert len(set(outs.values())) == 1, outs


def test_stdout_carries_ids_only(workspace):
    proc = run_cli(workspace, "json", "--terms", "whale ship", "--mode", "or")
    assert proc.returncode == 0
    assert proc.stdout == "11\n22\n33\n"


def test_no_match_prints_nothing_and_exits_zero(workspace):
    proc = run_cli(workspace, "json", "--terms", "kangaroo", "--mode", "and")
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_stop_words_only_is_not_an_error(workspace):
    proc = run_cli(workspace, "json", "--terms", "the of and", "--mode", "or")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "filtered out" in proc.stderr


# ------------------------------------------------------------- exit codes


def test_missing_index_is_an_error(tmp_path):
    proc = run_cli(tmp_path, "json", "--terms", "whale", "--mode", "and")
    assert proc.returncode == 1
    assert "index" in proc.stderr


def test_bad_mode_is_a_usage_error(workspace):
    proc = run_cli(workspace, "json", "--terms", "whale", "--mode", "xor")
    assert proc.returncode == 2


def test_missing_terms_is_a_usage_error(workspace):
    proc = run_cli(workspace, "json", "--mode", "and")
    assert proc.returncode == 2


def test_negative_limit_is_a_usage_error(workspace):
    proc = run_cli(workspace, "json", "--terms", "whale", "--mode", "and", "--limit", "-1")
    assert proc.returncode == 2


def test_version_exits_zero(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(CLI), "--workspace", str(tmp_path), "version"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    expected = (REPO / "spec" / "SPEC_VERSION").read_text(encoding="utf-8").strip()
    assert proc.stdout == f"{expected}\n"


def test_spec_version_matches_the_repository():
    # Fails the day spec/SPEC_VERSION is bumped without updating this code.
    assert check_spec_version() is None


def test_spec_mismatch_is_detected(tmp_path):
    fake = tmp_path / "SPEC_VERSION"
    fake.write_text("9.9.9\n", encoding="utf-8")
    assert "mismatch" in check_spec_version(fake)


def test_unreadable_spec_version_is_detected(tmp_path):
    assert "cannot read" in check_spec_version(tmp_path / "missing")


def test_unimplemented_command_names_its_issue(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(CLI), "--workspace", str(tmp_path), "reconcile"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "issue #" in proc.stderr
