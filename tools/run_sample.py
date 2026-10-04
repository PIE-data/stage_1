"""Run the committed sample dataset offline, in Python or Node."""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "sample"
STAMP = "2026-01-01T00:00:00Z"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="New directory to create; defaults to a temporary workspace.",
    )
    parser.add_argument(
        "--layout", choices=["time", "book", "hash"], default="hash"
    )
    parser.add_argument(
        "--backend", choices=["json", "folder", "sqlite"], default="sqlite"
    )
    parser.add_argument(
        "--lang",
        choices=["python", "node", "go"],
        default="python",
        help="The language implementation to run.",
    )
    args = parser.parse_args()

    if args.lang == "node":
        executable = shutil.which("node")
        if executable is None:
            parser.error("Node.js is required to run the Node sample.")
        command = [executable, str(REPO / "src" / "node" / "cli.js")]
    elif args.lang == "go":
        if shutil.which("go") is None:
            parser.error("Go is required to run the Go sample.")
        binary = Path(tempfile.mkdtemp(prefix="stage1-go-")) / "engine"
        print(f"Building the Go CLI into {binary}", flush=True)
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/engine"],
                       cwd=REPO / "src" / "go", check=True)
        command = [str(binary)]
    else:
        command = [sys.executable, str(REPO / "src" / "python" / "cli.py")]

    ids = [
        int(line)
        for line in (SAMPLE / "manifest.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not ids or len(ids) != len(set(ids)) or any(i <= 0 for i in ids):
        parser.error("The sample manifest must contain distinct positive IDs.")

    for book_id in ids:
        if not (SAMPLE / f"{book_id}.txt").is_file():
            parser.error(f"Missing sample book: {book_id}.txt")

    if args.workspace is None:
        workspace = Path(tempfile.mkdtemp(prefix="stage1-sample-"))
    else:
        workspace = args.workspace.resolve()
        if workspace.exists():
            parser.error("--workspace must name a directory that does not exist.")
        workspace.mkdir(parents=True)

    print(f"Workspace: {workspace}", flush=True)
    print("Artifacts are retained after the demo.", flush=True)

    raw = workspace / "raw"
    raw.mkdir()
    for book_id in ids:
        shutil.copyfile(SAMPLE / f"{book_id}.txt", raw / f"{book_id}.txt")

    engine = [
        *command,
        "--workspace", str(workspace),
        "--datalake-layout", args.layout,
        "--index-backend", args.backend,
        "--now", STAMP,
    ]

    def run(*arguments):
        print(f"\n> {' '.join(arguments)}", flush=True)
        subprocess.run(
            [*engine, *arguments],
            cwd=REPO,
            check=True,
        )

    run("version")

    for book_id in ids:
        run("split", "--book-id", str(book_id))

    run("metadata", "--all")
    run("index", "--all", "--positions")
    run("lookup", "--book-id", str(ids[0]))
    run("query", "--terms", "says", "--mode", "and")
    run("scan-new")

    canonical = workspace / "canonical.json"
    run("export-canonical", "--out", str(canonical))

    # The frozen golden hash covers 20 books, not this three-book sample.
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    print(f"\nSample canonical SHA-256: {digest}")
    print(f"SUCCESS: {len(ids)} books processed without network access.")
    print(f"Inspect the results in: {workspace}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print(
            f"Sample command failed with exit code {error.returncode}.",
            file=sys.stderr,
        )
        sys.exit(error.returncode)