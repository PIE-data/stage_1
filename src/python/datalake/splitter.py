"""
Splitter and body cleaner.  SPEC.md §2.2 and §2.3.

Takes the raw text of a Project Gutenberg book and separates it into the
header (the bibliographic block Gutenberg puts in front) and the body (the
work itself).  The licence footer is discarded.

Two rules here are worth more than they look:

  * The END marker is matched at its LAST occurrence, not its first.  Some
    books quote the marker inside their own text; taking the first occurrence
    truncates the book and reports no error at all.  The failure is silent,
    which is why `spec/golden/synthetic_quoted_end.txt` exists.

  * Cleaning is exhaustive and ordered (§2.3).  Do not add a step, however
    sensible it looks: the cleaned bytes feed the tokenizer, whose output feeds
    the canonical hash, and every implementation must produce the same bytes.
    A step added here has to be added identically in Node and in Go.

Marker matching is a plain case-sensitive substring search, so it behaves the
same in all three languages.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "START_MARKER",
    "END_MARKER",
    "MarkersNotFound",
    "split_text",
    "split_file",
    "clean_body",
    "clean_header",
]

# SPEC.md §2.2 -- three asterisks, not two, and no trailing part of the line:
# the title that follows the marker differs from book to book.
START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

BOM = "﻿"

_THREE_OR_MORE_NEWLINES = re.compile(r"\n{3,}")


class MarkersNotFound(Exception):
    """Raised when START or END is missing, or they appear out of order.

    The caller maps this to exit code 3 and appends
    `<id>\\tNO_MARKERS\\t<ISO8601>` to `control/failed_books.txt` (SPEC.md
    §2.2).  Nothing is written to the datalake for that book.
    """


def _line_start(text: str, index: int) -> int:
    """Index of the first character of the line containing `index`."""
    return text.rfind("\n", 0, index) + 1


def _line_end(text: str, index: int) -> int:
    """Index just past the newline that terminates the line containing `index`.

    A marker on the final line of a file without a trailing newline yields
    len(text), so the body is simply empty rather than an error.
    """
    nl = text.find("\n", index)
    return len(text) if nl == -1 else nl + 1


def split_text(text: str) -> tuple[str, str]:
    """Split raw book text into (header, body), both cleaned per §2.3.

    Raises MarkersNotFound if either marker is absent or END precedes START.
    """
    start = text.find(START_MARKER)          # first occurrence
    if start == -1:
        raise MarkersNotFound("START marker not found")

    end = text.rfind(END_MARKER)             # LAST occurrence -- see module docstring
    if end == -1:
        raise MarkersNotFound("END marker not found")

    body_start = _line_end(text, start)
    body_end = _line_start(text, end)
    if body_end < body_start:
        raise MarkersNotFound("END marker precedes START marker")

    header = text[: _line_start(text, start)]
    body = text[body_start:body_end]
    return clean_header(header), clean_body(body)


def split_file(path: str | Path) -> tuple[str, str]:
    """Read a cached raw book and split it.

    Decoding matches §2.1: UTF-8 with replacement of invalid sequences, because
    Gutenberg occasionally serves Latin-1 mislabelled.  The replacement
    character then reaches the tokenizer identically in all three languages.
    """
    raw = Path(path).read_bytes()
    return split_text(raw.decode("utf-8", errors="replace"))


def _normalise_newlines(text: str) -> str:
    """§2.3 steps 1-2: drop the BOM, then CRLF -> LF, then lone CR -> LF."""
    if text.startswith(BOM):
        text = text[len(BOM) :]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def clean_body(body: str) -> str:
    """§2.3, all six steps, in this exact order.  Exhaustive: add nothing."""
    text = _normalise_newlines(body)                                   # 1, 2
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))  # 3
    text = _THREE_OR_MORE_NEWLINES.sub("\n\n", text)                   # 4
    text = text.strip()                                                # 5
    return text + "\n" if text else ""                                 # 6


def clean_header(header: str) -> str:
    """§2.3 steps 1, 2, 5 and 6 only.

    The header keeps its own line structure: trailing-space stripping and the
    blank-line collapse would destroy the indentation that marks a wrapped
    field, which the metadata parser relies on.
    """
    text = _normalise_newlines(header).strip()
    return text + "\n" if text else ""
