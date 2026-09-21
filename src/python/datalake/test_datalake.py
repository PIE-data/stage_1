from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.python.datalake.atomic import atomic_write
from src.python.datalake.time_storage import TimeBasedStorage
"""
#### ATOMIC WRITE TESTS #####
"""

def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "sub" / "hello.txt"
    atomic_write(target, "Hello World!")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "Hello World!"
    part_file = target.with_name(f"{target.name}.part")
    assert not part_file.exists()

def test_atomic_write_overwrites_existing(tmp_path: Path):
    target = tmp_path / "file.txt"
    atomic_write(target, "First")
    atomic_write(target, "Second")

    assert target.read_text(encoding="utf-8") == "Second"

def test_atomic_write_cleans_up_on_error(tmp_path: Path, monkeypatch):
    target = tmp_path / "crash.txt"
    part_file = target.with_name(f"{target.name}.part")

    def broken_replace(src, dst):
        raise OSError("Simulated disk error during rename")

    monkeypatch.setattr("os.replace", broken_replace)

    with pytest.raises(OSError):
        atomic_write(target, "Anything")

    assert not part_file.exists()
    assert not target.exists()

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        print("Running test_atomic_write_creates_file...")
        test_atomic_write_creates_file(tmp_path)
        print("PASSED")

        print("Running test_atomic_write_overwrites_existing...")
        test_atomic_write_overwrites_existing(tmp_path)
        print("PASSED")

    print("\nAll atomic tests passed successfully!")


"""
##### TimeBasedStorage TESTS ####
"""

def test_time_storage_roundtrip(tmp_path: Path):
    now = datetime(2026, 9, 21, 14, 30, tzinfo=timezone.utc)
    storage = TimeBasedStorage(workspace=tmp_path, now=now)

    book_id = 1342
    header_content = "Title: Pride and Prejudice\nAuthor: Jane Austen\n"
    body_content = "It is a truth universally acknowledged...\n"

    rel_header, rel_body = storage.write(book_id, header_content, body_content)

    assert rel_header == "datalake/20260921/14/1342.header.txt"
    assert rel_body == "datalake/20260921/14/1342.body.txt"

    found = storage.lookup(book_id)

    assert found is not None

    found_header, found_body = found
    assert found_header == rel_header
    assert found_body == rel_body
    
    assert (tmp_path / found_header).read_text(encoding="utf-8") == header_content
    assert (tmp_path / found_body).read_text(encoding="utf-8") == body_content



def test_time_storage_lookup_missing(tmp_path: Path):
    storage = TimeBasedStorage(workspace=tmp_path)
    assert storage.lookup(99999) is None

def test_time_storage_list_new(tmp_path: Path):
    time_early = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    time_late = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)

    storage_early = TimeBasedStorage(workspace=tmp_path, now=time_early)
    storage_late = TimeBasedStorage(workspace=tmp_path, now=time_late)

    storage_early.write(1, "Header 1", "Body 1")
    storage_late.write(2, "Header 2", "Body 2")

    since_noon = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    new_books = list(storage_early.list_new(since_noon))
    assert new_books == [2]

    since_morning = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
    all_books = sorted(list(storage_early.list_new(since_morning)))
    assert all_books == [1, 2]
