"""
Tokenizer — the equivalence-critical component.  SPEC.md §3.

Three implementations (Python, Node, Go) must produce byte-identical token
streams from the same input.  That is why this is written as an explicit state
machine over Unicode code points and NOT as a regular expression: `\\w`, `\\b`
and `\\p{L}` mean different things in Python's `re`, JavaScript's regex engine
and Go's RE2, and Python's `re` has no `\\p{L}` at all.

Pipeline (SPEC.md §3.1), in this exact order:
    1. NFKC normalization
    2. locale-invariant lowercase
    3. ASCII folding: NFD -> drop category Mn -> NFC

Token extraction (§3.2):
    a word character is Unicode category Lu, Ll, Lt, Lm, Lo or Nd;
    U+0027 and U+2019 continue a token only when the code points on BOTH sides
    are word characters; anything else terminates the current token.

Filtering (§3.3), applied in order to each emitted token:
    length < 2 | length > 40 | all digits | present in the stop-word list

Positions are the 0-based ordinal of the token in the document and are assigned
BEFORE filtering, so that ordinals keep reflecting the real text.  Filtering
removes postings; it never renumbers them.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

__all__ = ["normalize", "extract_tokens", "tokenize", "load_stopwords"]

# SPEC.md §3.2 -- letters and decimal digits.
# Deliberately NOT str.isalnum(), which also accepts categories No and Nl
# (e.g. "½", "Ⅷ") that the spec excludes.
WORD_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd"})

# Both the ASCII apostrophe and the typographic one.
APOSTROPHES = frozenset({"'", "’"})

MIN_TOKEN_LEN = 2
MAX_TOKEN_LEN = 40


def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch) in WORD_CATEGORIES


def normalize(text: str) -> str:
    """SPEC.md §3.1. The order of these three steps is part of the contract."""
    # 1. NFKC: expands ligatures (fi -> fi), mathematical alphanumerics
    #    (A -> A), circled digits (1 -> 1), and much else.
    text = unicodedata.normalize("NFKC", text)

    # 2. Lowercase. str.lower(), NOT str.casefold(): casefold maps "ß" to "ss",
    #    while Go's strings.ToLower and JavaScript's toLowerCase do not. Using
    #    casefold here would make Python diverge from the other two.
    text = text.lower()

    # 3. ASCII folding: decompose, drop non-spacing marks, recompose.
    #    "café" -> "cafe", "naïve" -> "naive".
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", text)


def extract_tokens(text: str) -> list[str]:
    """
    SPEC.md §3.2 — every token, in order, before any filtering.

    The caller needs the unfiltered stream because token ordinals are assigned
    here and must survive filtering unchanged.
    """
    chars = list(normalize(text))
    n = len(chars)
    tokens: list[str] = []
    buf: list[str] = []

    for i, ch in enumerate(chars):
        if _is_word_char(ch):
            buf.append(ch)
            continue

        if (
            ch in APOSTROPHES
            and buf                                  # something to continue
            and i > 0 and _is_word_char(chars[i - 1])
            and i + 1 < n and _is_word_char(chars[i + 1])
        ):
            buf.append(ch)
            continue

        if buf:
            tokens.append("".join(buf))
            buf = []

    if buf:
        tokens.append("".join(buf))
    return tokens


def _is_all_digits(token: str) -> bool:
    # Category Nd only. str.isdigit() would also accept superscripts, but those
    # are category No and can never appear inside a token anyway -- being
    # explicit keeps this readable next to the Node and Go ports.
    return all(unicodedata.category(ch) == "Nd" for ch in token)


def tokenize(text: str, stopwords: frozenset[str] | set[str]) -> list[tuple[str, int]]:
    """
    SPEC.md §3.3 — filtered tokens paired with their pre-filter ordinal.

    Returns [(token, position), ...] where position counts every token the
    document produced, including the ones filtered out here.
    """
    kept: list[tuple[str, int]] = []
    for position, token in enumerate(extract_tokens(text)):
        if len(token) < MIN_TOKEN_LEN:
            continue
        if len(token) > MAX_TOKEN_LEN:
            continue
        if _is_all_digits(token):
            continue
        if token in stopwords:
            continue
        kept.append((token, position))
    return kept


def load_stopwords(path: str | Path) -> frozenset[str]:
    """
    SPEC.md §3.3 — one lowercase term per line, UTF-8, LF.
    Blank lines and lines starting with '#' are ignored.
    """
    terms = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.add(line)
    return frozenset(terms)
