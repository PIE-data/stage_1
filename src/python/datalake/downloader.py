import time

import requests

HEADERS = {"User-Agent": "ULPGC-BigData-Stage1/PIE-data (+https://github.com/PIE-data/stage_1)"}

# SPEC.md §2.1: 3 retries after the first attempt, backoff 1 s / 2 s / 4 s,
# on 5xx and network errors only. Never retry 404.
RETRIES = 3
BACKOFF = (1.0, 2.0, 4.0)
TIMEOUT = (30.0, 60.0)  # connect, read

# One session for the whole run. Node's fetch and Go's net/http reuse
# connections by default; without a Session Python would open a new
# connection per book, and experiment E1 would measure that, not the language.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def download(book_id: int,
             source_base: str = "https://www.gutenberg.org") -> str:
    """
    Downloads raw plain-text for a Gutenberg book.
    Returns the decoded text, or raises on permanent failure.
    """
    base = source_base.rstrip("/")
    url = f"{base}/cache/epub/{book_id}/pg{book_id}.txt"
    last_error: Exception | None = None

    for attempt in range(RETRIES + 1):
        try:
            response = SESSION.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code == 200:
                return response.content.decode("utf-8", errors="replace")
            if response.status_code == 404:
                raise FileNotFoundError(f"Book {book_id} not found: 404")
            if response.status_code < 500:
                raise RuntimeError(
                    f"Client error downloading {book_id}: HTTP {response.status_code}"
                )
            last_error = RuntimeError(f"HTTP {response.status_code}")

        if attempt < RETRIES:  # no pointless wait after the last attempt
            time.sleep(BACKOFF[attempt])

    raise RuntimeError(
        f"Failed to download {book_id} after {RETRIES + 1} attempts"
    ) from last_error
