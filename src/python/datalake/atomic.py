import os
from pathlib import Path


def atomic_write(target_path: Path | str,
                 content: str,
                 encoding: str = "utf-8") -> None:
    """
    SPEC.md §2.4: write <target>.part -> flush -> fsync -> rename -> fsync dir.

    Bytes, not text mode: in text mode Windows turns "\\n" into "\\r\\n", so the
    stored file would no longer be byte-identical to what was written.
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part_path = target.with_name(f"{target.name}.part")
    data = content.encode(encoding)

    try:
        with open(part_path, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(part_path, target)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    _fsync_dir(target.parent)


def _fsync_dir(directory: Path) -> None:
    """Persist the rename on POSIX. Windows cannot open a directory; there
    the rename is durable on its own, so a failure here is not an error."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
