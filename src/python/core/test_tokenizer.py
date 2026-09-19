"""
Tokenizer conformance tests.  SPEC.md §3.

Every expected value here was derived by reading the spec, not by running the
implementation. That direction matters: these tests are the only place where a
human decides what is correct. The golden fixture (tokens_20.jsonl) is
generated FROM this implementation, so if the implementation is wrong the
golden hash is wrong too, and Node and Go will faithfully reproduce the same
mistake while all nine hashes agree.

    python -m pytest src/python/core/test_tokenizer.py -v
"""

from __future__ import annotations

import pytest

from tokenizer import extract_tokens, load_stopwords, normalize, tokenize

# A minimal stop-word set for the unit tests. The real list lives in
# spec/stopwords_en.txt; hard-coding a few here keeps these tests independent
# of changes to it.
STOP = frozenset({"the", "a", "i", "don't", "is", "and", "of"})


# --------------------------------------------------------------- normalization

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("café", "cafe"),                       # accent stripped
        ("naïve", "naive"),
        ("Les Misérables", "les miserables"),
        ("ﬁnd the ﬂow", "find the flow"),       # NFKC expands ligatures
        ("①②③", "123"),                          # NFKC expands circled digits
        ("ＡＢＣ", "abc"),                        # NFKC folds fullwidth forms
        ("straße", "straße"),                   # lower(), not casefold()
        ("ÅNGSTRÖM", "angstrom"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_lower_not_casefold():
    """casefold() would give 'strasse' and diverge from Go and JavaScript."""
    assert normalize("STRASSE") != normalize("STRASSE".replace("SS", "ß"))
    assert normalize("ß") == "ß"


# ------------------------------------------------------------ token extraction

@pytest.mark.parametrize(
    "raw, expected",
    [
        # apostrophe joins only between two word characters
        ("don't", ["don't"]),
        ("rock'n'roll", ["rock'n'roll"]),
        ("'tis the season", ["tis", "the", "season"]),      # leading: no join
        ("the dogs' bones", ["the", "dogs", "bones"]),      # trailing: no join
        ("a''b", ["a", "b"]),                                # doubled: no join
        ("don’t", ["don’t"]),                      # typographic apostrophe
        # punctuation and whitespace terminate tokens
        ("Hello, world!", ["hello", "world"]),
        ("one--two", ["one", "two"]),
        ("line\nbreak", ["line", "break"]),
        # digits are word characters; letters and digits mix freely
        ("chapter12", ["chapter12"]),
        ("1984", ["1984"]),
        # empty and punctuation-only input
        ("", []),
        ("!!! ??? ...", []),
    ],
)
def test_extract_tokens(raw, expected):
    assert extract_tokens(raw) == expected


def test_extract_is_pre_filter():
    """extract_tokens returns everything, including what tokenize() drops."""
    assert extract_tokens("the a 1234") == ["the", "a", "1234"]


# ------------------------------------------------------------------- filtering

@pytest.mark.parametrize(
    "raw, expected",
    [
        # length < 2
        ("a I x", []),
        # length > 40
        ("x" * 41, []),
        ("x" * 40, [("x" * 40, 0)]),                  # boundary: 40 is kept
        ("x" * 2, [("xx", 0)]),                       # boundary: 2 is kept
        # all digits
        ("12345", []),
        ("①②③", []),                                   # NFKC -> "123" -> digits
        ("chapter12", [("chapter12", 0)]),            # mixed is kept
        # stop words
        ("the of and", []),
    ],
)
def test_filtering(raw, expected):
    assert tokenize(raw, STOP) == expected


# ------------------------------------------------------------------- positions

def test_positions_are_assigned_before_filtering():
    """
    "the cat the dog" produces four tokens: the(0) cat(1) the(2) dog(3).
    Removing the stop words must not renumber cat and dog.
    """
    assert tokenize("the cat the dog", STOP) == [("cat", 1), ("dog", 3)]


def test_positions_survive_every_filter():
    # a(0) is too short, 1234(1) is digits, the(2) is a stop word,
    # so only house(3) survives -- at position 3.
    assert tokenize("a 1234 the house", STOP) == [("house", 3)]


# ------------------------------------------------------------------ end to end

def test_spec_example_sentence():
    """
    "The naïve Cafés don't open."
      normalize -> "the naive cafes don't open."
      tokens    -> the(0) naive(1) cafes(2) don't(3) open(4)
      filter    -> "the" and "don't" are stop words
    """
    assert tokenize("The naïve Cafés don't open.", STOP) == [
        ("naive", 1),
        ("cafes", 2),
        ("open", 4),
    ]


def test_determinism():
    text = "The quick brown fox — it's café-bound, isn't it?"
    assert tokenize(text, STOP) == tokenize(text, STOP)


# ------------------------------------------------------------------ stop words

def test_load_stopwords(tmp_path):
    p = tmp_path / "sw.txt"
    p.write_text("# comment\n\nthe\nand\n  of  \n", encoding="utf-8")
    assert load_stopwords(p) == frozenset({"the", "and", "of"})
