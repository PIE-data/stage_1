import re
from datetime import datetime, timezone
from pathlib import Path

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

def clean_body(body: str) -> str:
    # 1. Strip UTF-8 BOM if present
    body = body.removeprefix("\ufeff")
    # 2. \r\n -> \n, then lone \r -> \n
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    # 3. Strip trailing spaces and tabs from every line
    body = "\n".join(line.rstrip(" \t") for line in body.split("\n"))
    # 4. Collapse runs of 3+ consecutive newlines to exactly 2
    body = re.sub(r"\n{3,}", "\n\n", body)
    # 5. Strip leading and trailing whitespace from the whole string
    body = body.strip()
    # 6. Ensure the result ends with exactly one \n
    return body + "\n"

def clean_header(header: str) -> str:
    header = header.removeprefix("\ufeff")
    header = header.replace("\r\n", "\n").replace("\r", "\n")
    header = header.strip()
    return header + "\n"

def split(raw: str) -> tuple[str, str] | None:
    """
    Splits raw Gutenberg text into (header, body).
    First occurrence of START, LAST occurrence of END.
    Returns None if either marker is missing.
    """
    start_idx = raw.find(START_MARKER)
    end_idx = raw.rfind(END_MARKER)

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    start_line_begin = raw.rfind("\n", 0, start_idx)
    header_raw = raw[:start_line_begin] if start_line_begin != -1 else ""

    start_line_end = raw.find("\n", start_idx)
    if start_line_end == -1:
        return None

    end_line_begin = raw.rfind("\n", 0, end_idx)
    if end_line_begin == -1 or end_line_begin <= start_line_end:
        return None
    body_raw = raw[start_line_end + 1:end_line_begin]
    return clean_header(header_raw), clean_body(body_raw)

def log_failed_book(control_dir: Path | str, book_id: int, reason: str = "NO_MARKERS") -> None:
    """
    Appends <id>\t<reason>\t<ISO8601> to control/failed_books.txt.
    """
    control_path = Path(control_dir)
    control_path.mkdir(parents=True, exist_ok=True)
    failed_file = control_path / "failed_books.txt"
    iso_now = datetime.now(timezone.utc).isoformat()
    with open(failed_file, "a", encoding="utf-8") as f:
        f.write(f"{book_id}\t{reason}\t{iso_now}\n")
