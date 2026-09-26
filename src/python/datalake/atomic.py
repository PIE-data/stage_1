from pathlib import Path
import os

def atomic_write(target_path: Path | str,
                 content: str,
                 encoding: str = "utf-8") -> None:
    """
    Writes content to target_path atomically.
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok = True)

    part_path = target.with_name(f"{target.name}.part")
    
    try:
        with open(part_path, "w", encoding=encoding) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        os.replace(part_path, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise

