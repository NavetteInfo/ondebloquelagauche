#!/usr/bin/env python3
"""Sert le site et refait le merge en boucle."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from refresh import FICHES, refresh_once

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
REFRESH_SECONDS = max(15, int(os.environ.get("REFRESH_SECONDS", "60")))

_lock = threading.Lock()
_status = {
    "ok": False,
    "as_of": None,
    "refreshed_at": None,
    "total_blocks": None,
    "fiche_count": None,
    "error": None,
    "refresh_seconds": REFRESH_SECONDS,
}


def _remember(payload: dict | None, error: str | None) -> None:
    with _lock:
        _status["ok"] = error is None and payload is not None
        _status["error"] = error
        if payload:
            _status["as_of"] = payload.get("as_of")
            _status["refreshed_at"] = payload.get("refreshed_at")
            _status["total_blocks"] = payload.get("total_blocks")
            _status["fiche_count"] = payload.get("fiche_count")


def _seed_from_disk() -> None:
    if not FICHES.exists():
        return
    try:
        payload = json.loads(FICHES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    _remember(payload, None)
    with _lock:
        _status["ok"] = False
        _status["error"] = None


def _loop() -> None:
    while True:
        try:
            payload = refresh_once()
            _remember(payload, None)
            print(
                f"relevé ok fiches={payload['fiche_count']} "
                f"total={payload['total_blocks']} as_of={payload['as_of']}",
                flush=True,
            )
        except Exception as exc:
            _remember(None, str(exc))
            print(f"relevé raté: {exc}", flush=True)
        time.sleep(REFRESH_SECONDS)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/data/") or path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/status":
            with _lock:
                body = json.dumps(_status, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        if len(args) >= 2 and str(args[1]) in {"200", "304"}:
            return
        super().log_message(fmt, *args)


def main() -> None:
    _seed_from_disk()
    threading.Thread(target=_loop, name="refresh", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"site sur http://{HOST}:{PORT}  relevé toutes les {REFRESH_SECONDS}s", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
