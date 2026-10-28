#!/usr/bin/env python3
"""Sert le site, refait le merge, et fait attendre le surplus."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from refresh import FICHES, refresh_once

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
REFRESH_SECONDS = max(15, int(os.environ.get("REFRESH_SECONDS", "60")))
PUBLIC_ORIGIN = "https://navetteinfo.github.io/ondebloquelagauche"

# Deux places en même temps. Le reste fait la queue, puis repart avec un 429.
MAX_INFLIGHT = max(1, int(os.environ.get("MAX_INFLIGHT", "2")))
MAX_QUEUE = max(0, int(os.environ.get("MAX_QUEUE", "12")))
QUEUE_WAIT = max(1.0, float(os.environ.get("QUEUE_WAIT", "20")))
IP_CAPACITY = max(1, int(os.environ.get("IP_CAPACITY", "16")))
IP_REFILL = max(0.05, float(os.environ.get("IP_REFILL", "0.25")))
IP_WAIT = max(0.0, float(os.environ.get("IP_WAIT", "12")))

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

_inflight = threading.BoundedSemaphore(MAX_INFLIGHT)
_queue_lock = threading.Lock()
_queued = 0
_bucket_lock = threading.Lock()
_buckets: dict[str, tuple[float, float]] = {}


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


def _take_token(ip: str) -> tuple[bool, int]:
    """Consomme un jeton. S'il n'y en a pas, le fil attend, puis abandonne."""
    deadline = time.monotonic() + IP_WAIT
    while True:
        now = time.monotonic()
        with _bucket_lock:
            tokens, last = _buckets.get(ip, (float(IP_CAPACITY), now))
            tokens = min(float(IP_CAPACITY), tokens + (now - last) * IP_REFILL)
            if tokens >= 1:
                _buckets[ip] = (tokens - 1, now)
                if len(_buckets) > 4000:
                    stale = [key for key, (left, seen) in _buckets.items() if now - seen > 600 and left >= IP_CAPACITY - 1]
                    for key in stale[:2000]:
                        _buckets.pop(key, None)
                return True, 0
            missing = (1 - tokens) / IP_REFILL
            _buckets[ip] = (tokens, now)
        if now + missing > deadline:
            return False, max(1, int(missing + 0.999))
        time.sleep(min(missing, 0.4, max(0.0, deadline - now)))


def _refund(ip: str) -> None:
    with _bucket_lock:
        tokens, last = _buckets.get(ip, (0.0, time.monotonic()))
        _buckets[ip] = (min(float(IP_CAPACITY), tokens + 1), last)


def _enter(ip: str) -> tuple[bool, int]:
    global _queued
    allowed, retry = _take_token(ip)
    if not allowed:
        return False, retry
    with _queue_lock:
        if _queued >= MAX_QUEUE:
            _refund(ip)
            return False, int(QUEUE_WAIT)
        _queued += 1
    acquired = False
    try:
        acquired = _inflight.acquire(timeout=QUEUE_WAIT)
    finally:
        with _queue_lock:
            _queued -= 1
    if not acquired:
        _refund(ip)
        return False, int(QUEUE_WAIT)
    return True, 0


def _leave() -> None:
    _inflight.release()


WAIT_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>Tu attendras</title>
  <style>
    body {{ margin: 0; background: #eae8e3; color: #12110f; font-family: "IBM Plex Mono", "Liberation Mono", ui-monospace, monospace; }}
    .bar {{ height: 10px; background: #8e0e0e; }}
    main {{ padding: 2.2rem 1.2rem; max-width: 38rem; }}
    h1 {{ margin: 0 0 0.8rem; font-family: "Archivo Black", "Liberation Sans", sans-serif; font-size: clamp(2.4rem, 8vw, 4.2rem); line-height: 0.9; letter-spacing: -0.04em; text-transform: uppercase; }}
    p {{ font-size: 1rem; font-weight: 700; }}
  </style>
</head>
<body>
  <div class="bar"></div>
  <main>
    <h1>Tu attendras.</h1>
    <p>Deux places. Pas de répartiteur de charge. Réessaie dans {seconds} secondes.</p>
  </main>
</body>
</html>
"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _origin(self) -> str:
        forced = os.environ.get("SITE_URL", "").strip().rstrip("/")
        if forced:
            return forced
        host = self.headers.get("Host", "")
        if not re.fullmatch(r"[A-Za-z0-9.:-]{1,200}", host or ""):
            return PUBLIC_ORIGIN
        return f"http://{host}"

    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/data/") or path.startswith("/api/") or path in {"/", "/index.html"}:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _bytes(self, code: int, body: bytes, content_type: str, retry: int | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if retry is not None:
            self.send_header("Retry-After", str(retry))
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _wait_page(self, retry: int) -> None:
        page = WAIT_PAGE.format(seconds=int(retry)).encode("utf-8")
        self._bytes(429, page, "text/html; charset=utf-8", retry)

    def _serve_text(self, text: str, content_type: str) -> None:
        body = text.replace(PUBLIC_ORIGIN, self._origin()).encode("utf-8")
        self._bytes(200, body, content_type)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            with _lock:
                body = json.dumps(_status, ensure_ascii=False).encode("utf-8")
            self._bytes(200, body, "application/json; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self._serve_text((ROOT / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
            return
        if path == "/robots.txt":
            origin = self._origin()
            text = f"User-agent: *\nAllow: /\n\nSitemap: {origin}/sitemap.xml\n"
            self._bytes(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        if path == "/sitemap.xml":
            origin = self._origin()
            text = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"  <url><loc>{origin}/</loc></url>\n"
                "</urlset>\n"
            )
            self._bytes(200, text.encode("utf-8"), "application/xml; charset=utf-8")
            return
        super().do_GET()

    def do_GET(self) -> None:
        ip = self.client_address[0]
        allowed, retry = _enter(ip)
        if not allowed:
            self._wait_page(retry)
            return
        try:
            self._route()
        finally:
            _leave()

    def do_HEAD(self) -> None:
        ip = self.client_address[0]
        allowed, retry = _enter(ip)
        if not allowed:
            self.send_response(429)
            self.send_header("Retry-After", str(int(retry)))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            super().do_HEAD()
        finally:
            _leave()

    def log_message(self, fmt: str, *args) -> None:
        code = str(args[1]) if len(args) >= 2 else ""
        if code in {"200", "304"}:
            return
        super().log_message(fmt, *args)


def main() -> None:
    _seed_from_disk()
    threading.Thread(target=_loop, name="refresh", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        f"site sur http://{HOST}:{PORT}  relevé toutes les {REFRESH_SECONDS}s  "
        f"file {MAX_INFLIGHT} places, {MAX_QUEUE} en attente",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
