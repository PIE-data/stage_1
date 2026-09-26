import time
import requests

HEADERS = {"User-Agent": "ULPGC-BigData-Stage1/PIE-data (+https://github.com/PIE-data/stage_1)"}

def download(book_id: int,
             source_base: str = "https://www.gutenberg.org") -> str:
    """
    Downloads raw plain-text for a Gutenberg book.
    Returns a decoded text, or raises an exception on permanent failure.
    """
    base = source_base.rstrip("/")
    url = f"{base}/cache/epub/{book_id}/pg{book_id}.txt"
    
    delay = 1.0
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=(30.0, 60.0))

            if response.status_code == 200:
                return response.content.decode("utf-8", errors="replace")
            
            if response.status_code == 404:
                raise FileNotFoundError(f"Book {book_id} not found: 404")

            if 400 <= response.status_code < 500:
                raise RuntimeError(f"Client error downloading {book_id}: HTTP {response.status_code}")

            if response.status_code >= 500:
                time.sleep(delay)
                delay *= 2
                continue
        except requests.RequestException:
            time.sleep(delay)
            delay *= 2

    raise RuntimeError("Failed to download after 3 attempts")
