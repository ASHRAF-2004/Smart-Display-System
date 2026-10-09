"""Loopback-only server for the untouched desktop visual baseline."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".backup" / "youruni_tv_task_management_system update 17 Aug.original.html"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = BASELINE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    if not BASELINE.is_file():
        raise SystemExit(f"Baseline missing: {BASELINE}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
