#!/usr/bin/env python3
"""
A local stand-in for Gutenberg, for the conformance gate and the benchmarks.

    python tools/mirror_server.py --root spec/golden --port-file /tmp/port

Serves <root>/<id>.txt at Gutenberg's own path, /cache/epub/<id>/pg<id>.txt
(SPEC.md §2.1), so every implementation is driven through its real downloader
with `--source-base http://127.0.0.1:<port>` -- no network, no special case.
Anything else is a 404.  Binds 127.0.0.1 only; the chosen port is written to
--port-file once the server is listening, so a script can wait for it.
"""

from __future__ import annotations

import argparse
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PATH = re.compile(r"^/cache/epub/(\d+)/pg(\d+)\.txt$")


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            m = PATH.match(self.path)
            book = root / f"{m.group(1)}.txt" if m and m.group(1) == m.group(2) else None
            if book is None or not book.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = book.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="directory holding <id>.txt files")
    ap.add_argument("--port", type=int, default=0, help="0 = any free port")
    ap.add_argument("--port-file", help="write the chosen port here once listening")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Path(args.root)))
    port = server.server_address[1]
    if args.port_file:
        Path(args.port_file).write_text(f"{port}\n")
    print(f"mirror: http://127.0.0.1:{port}  root={args.root}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
