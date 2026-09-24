import pytest
from src.python.datalake.splitter import split, clean_body, clean_header, START_MARKER, END_MARKER
from src.python.datalake.splitter import log_failed_book

def test_split_standard_book():
    raw = (
        "Title: Example Book\n"
        "Author: John Doe\n"
        f"{START_MARKER} EXAMPLE BOOK ***\n"
        "Chapter 1\n"
        "This is the actual story.\n"
        f"{END_MARKER} EXAMPLE BOOK ***\n"
        "Footer and legal notes\n"
    )
    res = split(raw)
    assert res is not None
    header, body = res

    assert "Title: Example Book" in header
    assert "This is the actual story." in body
    assert START_MARKER not in header
    assert END_MARKER not in body


def test_split_marker_quoted_inside_body():
    """Acceptance Criteria: using LAST occurrence of END marker."""
    raw = (
        "Header text\n"
        f"{START_MARKER} BOOK ***\n"
        "Line 1\n"
        f"Quoting the end here: {END_MARKER}\n"
        "Line 2 continuing the story\n"
        f"{END_MARKER} BOOK ***\n"
        "End of file"
    )
    res = split(raw)
    assert res is not None
    _, body = res

    # The quoted marker must remain part of the body!
    assert "Quoting the end here" in body
    assert "Line 2 continuing the story" in body


def test_split_missing_start_marker():
    raw = f"Header\nBody text\n{END_MARKER}"
    assert split(raw) is None


def test_split_missing_end_marker():
    """Acceptance Criteria: missing END marker returns None."""
    raw = f"Header\n{START_MARKER}\nBody text"
    assert split(raw) is None


def test_clean_body_removes_bom_and_normalizes():
    raw_body = "\ufeffLine 1   \r\n\r\n\r\n\r\nLine 2 \t\r\n"
    cleaned = clean_body(raw_body)

    # 1. No BOM
    assert not cleaned.startswith("\ufeff")
    # 2. No \r
    assert "\r" not in cleaned
    # 3. Trailing spaces on lines removed
    assert "Line 1\n" in cleaned
    # 4. Runs of 4 newlines collapsed to 2 (\n\n)
    assert "\n\n\n" not in cleaned
    # 6. Ends with single \n
    assert cleaned.endswith("Line 2\n")

def test_log_failed_book(tmp_path):
    control_dir = tmp_path / "control"
    log_failed_book(control_dir, 1342, "NO_MARKERS")
    failed_file = control_dir / "failed_books.txt"
    assert failed_file.exists()
    content = failed_file.read_text(encoding="utf-8")
    assert content.startswith("1342\tNO_MARKERS\t")
