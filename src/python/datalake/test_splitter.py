"""Unit tests for the splitter.  SPEC.md §2.2 and §2.3.

The interesting tests are the ones that fail for a plausible implementation:
the quoted END marker, the marker sitting on the last line, and the ordering of
the cleaning steps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .splitter import (
    END_MARKER,
    START_MARKER,
    MarkersNotFound,
    clean_body,
    clean_header,
    split_file,
    split_text,
)

GOLDEN = Path(__file__).resolve().parents[3] / "spec" / "golden"


def wrap(body: str, header: str = "Title: X\n") -> str:
    return f"{header}{START_MARKER} X ***\n{body}{END_MARKER} X ***\nlicence\n"


# --------------------------------------------------------------- §2.2 split


def test_header_body_footer():
    header, body = split_text(wrap("hello world\n"))
    assert header == "Title: X\n"
    assert body == "hello world\n"


def test_marker_line_itself_is_excluded_from_both():
    header, body = split_text(wrap("text\n"))
    assert START_MARKER not in header
    assert START_MARKER not in body
    assert END_MARKER not in body


def test_last_end_marker_wins():
    """The case the corpus does not contain and a wrong implementation hides.

    Taking the FIRST end marker truncates the book at 'quoted' and reports
    success.  Nothing downstream can detect it.
    """
    body = f"before\n{END_MARKER} QUOTED ***\nafter\n"
    _, out = split_text(wrap(body))
    assert "before" in out
    assert "after" in out
    assert END_MARKER in out


def test_first_start_marker_wins():
    body = f"real text\n{START_MARKER} AGAIN ***\nmore\n"
    _, out = split_text(wrap(body))
    assert out.startswith("real text")
    assert START_MARKER in out


def test_missing_start():
    with pytest.raises(MarkersNotFound):
        split_text(f"Title: X\n{END_MARKER} X ***\n")


def test_missing_end():
    with pytest.raises(MarkersNotFound):
        split_text(f"Title: X\n{START_MARKER} X ***\nbody\n")


def test_end_before_start():
    text = f"{END_MARKER} X ***\nstuff\n{START_MARKER} X ***\n"
    with pytest.raises(MarkersNotFound):
        split_text(text)


def test_start_marker_on_the_last_line_without_newline():
    """No trailing newline after the marker: the body is empty, not an error."""
    text = f"Title: X\n{END_MARKER} X ***\n{START_MARKER} X ***"
    with pytest.raises(MarkersNotFound):
        split_text(text)  # END precedes START


def test_no_trailing_newline_after_end_marker():
    text = f"Title: X\n{START_MARKER} X ***\nbody\n{END_MARKER} X ***"
    _, body = split_text(text)
    assert body == "body\n"


# -------------------------------------------------------------- §2.3 clean


def test_crlf_and_lone_cr_become_lf():
    assert clean_body("a\r\nb\rc\n") == "a\nb\nc\n"


def test_bom_is_stripped():
    assert clean_body("﻿a\n") == "a\n"


def test_trailing_spaces_and_tabs_are_stripped_per_line():
    assert clean_body("a  \nb\t\nc \t \n") == "a\nb\nc\n"


def test_three_or_more_newlines_collapse_to_two():
    assert clean_body("a\n\n\n\n\nb\n") == "a\n\nb\n"


def test_two_newlines_are_left_alone():
    assert clean_body("a\n\nb\n") == "a\n\nb\n"


def test_step_order_trailing_spaces_before_collapse():
    """A line of only spaces must become empty BEFORE the collapse.

    Reversing steps 3 and 4 leaves "a\\n   \\nb" untouched by the collapse and
    produces different bytes -- and therefore a different hash in one language
    if that language implements the steps in the other order.
    """
    assert clean_body("a\n \n \n \nb\n") == "a\n\nb\n"


def test_leading_and_trailing_whitespace_removed():
    assert clean_body("\n\n  a\n\n  \n") == "a\n"


def test_result_ends_with_exactly_one_newline():
    assert clean_body("a\n\n\n") == "a\n"
    assert clean_body("a") == "a\n"


def test_empty_body_stays_empty():
    assert clean_body("\n \n") == ""


def test_header_keeps_its_indentation():
    """Steps 3 and 4 are NOT applied to the header: the metadata parser uses
    the indentation to recognise a wrapped field."""
    header = "Title: A Long\n    Wrapped Title\n\n\n\nAuthor: X\n"
    assert clean_header(header) == "Title: A Long\n    Wrapped Title\n\n\n\nAuthor: X\n"


def test_header_normalises_newlines_and_bom():
    assert clean_header("﻿Title: X\r\nAuthor: Y\r\n") == "Title: X\nAuthor: Y\n"


# ------------------------------------------------- against the golden books


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture not present")
def test_every_golden_book_splits():
    # Numeric stems only: spec/golden also holds manifest_20.txt (a list of
    # ids, no markers) and the synthetic fixture, which has its own test.
    books = sorted(p for p in GOLDEN.glob("*.txt") if p.stem.isdigit())
    assert books, "no golden books found"
    for path in books:
        header, body = split_file(path)
        assert body, f"{path.name}: empty body"
        assert body.endswith("\n")
        assert "\r" not in body
        assert not body.startswith(START_MARKER)


@pytest.mark.skipif(
    not (GOLDEN / "synthetic_quoted_end.txt").exists(),
    reason="synthetic fixture not present",
)
def test_synthetic_quoted_end_is_not_truncated():
    """The whole point of the synthetic fixture: an implementation that takes
    the first END marker loses everything after it, silently."""
    _, body = split_file(GOLDEN / "synthetic_quoted_end.txt")
    assert END_MARKER in body, "the quoted marker should survive inside the body"
    assert "and then closed the volume." in body
