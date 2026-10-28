#!/usr/bin/env python3
"""Sert le site, refait le merge, et fait attendre le surplus."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from refresh import FICHES, refresh_followers, refresh_once
from visits import countable, record_visit, snapshot as visit_snapshot

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
REFRESH_SECONDS = max(15, int(os.environ.get("REFRESH_SECONDS", "60")))
FOLLOWERS_SECONDS = max(3600, int(os.environ.get("FOLLOWERS_SECONDS", "3600")))
PUBLIC_ORIGIN = "https://navetteinfo.github.io/ondebloquelagauche"
_ORIGIN_RE = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?\Z")

# Deux places en même temps. Le reste fait la queue, puis repart avec un 429.
MAX_INFLIGHT = max(1, int(os.environ.get("MAX_INFLIGHT", "2")))
MAX_QUEUE = max(0, int(os.environ.get("MAX_QUEUE", "12")))
QUEUE_WAIT = max(1.0, float(os.environ.get("QUEUE_WAIT", "20")))
IP_CAPACITY = max(1, int(os.environ.get("IP_CAPACITY", "16")))
IP_REFILL = max(0.05, float(os.environ.get("IP_REFILL", "0.25")))
IP_WAIT = max(0.0, float(os.environ.get("IP_WAIT", "12")))
_SLOT_COUNT = min(64, MAX_INFLIGHT + max(MAX_QUEUE, 1))

_lock = threading.Lock()
_status = {
    "ok": False,
    "as_of": None,
    "refreshed_at": None,
    "total_blocks": None,
    "fiche_count": None,
    "error": None,
    "refresh_seconds": REFRESH_SECONDS,
    "followers_at": None,
}

_inflight = threading.BoundedSemaphore(MAX_INFLIGHT)
_slots = threading.BoundedSemaphore(_SLOT_COUNT)
_queue_lock = threading.Lock()
_queued = 0
_bucket_lock = threading.Lock()
_buckets: dict[str, tuple[float, float]] = {}

# Fichiers lisibles par un visiteur. Le reste du dépôt (.git, code, bruts, compteur) reste dehors.
_DIR_SUFFIX = {
    "css": {".css"},
    "js": {".js"},
    "fonts": {".woff2"},
    "media": {".jpg", ".jpeg", ".png", ".webp", ".svg"},
}
_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}
PAGE_CSP = (
    "default-src 'self'; base-uri 'none'; form-action 'self'; object-src 'none'; "
    "script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'"
)
WAIT_CSS = (
    "body{margin:0;background:#eae8e3;color:#12110f;"
    "font-family:\"IBM Plex Mono\",\"Liberation Mono\",ui-monospace,monospace}"
    ".bar{height:10px;background:#8e0e0e}"
    "main{padding:2.2rem 1.2rem;max-width:38rem}"
    "h1{margin:0 0 .8rem;font-family:\"Archivo Black\",\"Liberation Sans\",sans-serif;"
    "font-size:clamp(2.4rem,8vw,4.2rem);line-height:.9;letter-spacing:-.04em;"
    "text-transform:uppercase}"
    "p{font-size:1rem;font-weight:700}"
)
_WAIT_HASH = base64.b64encode(hashlib.sha256(WAIT_CSS.encode("utf-8")).digest()).decode("ascii")
WAIT_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; "
    f"style-src 'sha256-{_WAIT_HASH}'; frame-ancestors 'none'"
)
FRAME_CSP = PAGE_CSP + "; frame-ancestors 'none'"


def bare_path(url_path: str) -> str:
    """Chemin du site, avec ou sans le préfixe de la copie Pages."""
    path = url_path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    prefix = urllib.parse.urlparse(PUBLIC_ORIGIN).path.rstrip("/")
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix):] or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def _fr_int(value: int) -> str:
    return f"{value:,}".replace(",", "\u202f")


def site_origin() -> str:
    """Origine publique. L'en-tête Host de la requête n'est pas une source."""
    forced = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if forced and _ORIGIN_RE.fullmatch(forced):
        return forced
    return PUBLIC_ORIGIN


def public_file(url_path: str) -> Path | None:
    raw = url_path.split("?", 1)[0].split("#", 1)[0]
    if "\\" in raw or "\x00" in raw:
        return None
    try:
        decoded = urllib.parse.unquote(raw, errors="strict")
    except UnicodeDecodeError:
        return None
    if "\\" in decoded or "\x00" in decoded:
        return None
    parts: list[str] = []
    for part in decoded.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith(".") or part != part.strip():
            return None
        parts.append(part)
    if not parts:
        return None
    rel = "/".join(parts)
    suffix = Path(parts[-1]).suffix.lower()
    allowed = rel in {"favicon.svg", "data/fiches.json", "data/submissions.json", "data/comparaison.json"} or (
        len(parts) >= 2 and parts[0] in _DIR_SUFFIX and suffix in _DIR_SUFFIX[parts[0]]
    )
    if not allowed:
        return None
    root = ROOT.resolve()
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        resolved = cursor.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not resolved.is_relative_to(root):
        return None
    if resolved.stat().st_size > 12_000_000:
        return None
    return resolved


def _remember(payload: dict | None, error: str | None) -> None:
    with _lock:
        _status["ok"] = error is None and payload is not None
        _status["error"] = error
        if payload:
            _status["as_of"] = payload.get("as_of")
            _status["refreshed_at"] = payload.get("refreshed_at")
            _status["total_blocks"] = payload.get("total_blocks")
            _status["fiche_count"] = payload.get("fiche_count")
            followers = payload.get("followers")
            if isinstance(followers, dict) and followers.get("latest"):
                _status["followers_at"] = followers.get("latest")


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
            _remember(None, "relevé raté")
            print(f"relevé raté: {exc}", flush=True)
        time.sleep(REFRESH_SECONDS)


def _follower_loop() -> None:
    """Relit les abonnés tout de suite, puis toutes les heures. N'appelle pas l'API des blocages."""
    while True:
        try:
            info = refresh_followers()
            with _lock:
                _status["followers_at"] = info.get("latest")
            print(
                f"abonnés ok fiches={info['matched']} écarts={info['compared']} "
                f"latest={info['latest']}",
                flush=True,
            )
        except Exception as exc:
            print(f"abonnés ratés: {exc}", flush=True)
        time.sleep(FOLLOWERS_SECONDS)


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
                    stale = [
                        key
                        for key, (left, seen) in _buckets.items()
                        if now - seen > 600 and left >= IP_CAPACITY - 1
                    ]
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


class Handler(SimpleHTTPRequestHandler):
    timeout = 20
    server_version = "OnDebloque"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        self._csp = FRAME_CSP
        self._cache = "no-store"
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def version_string(self) -> str:
        return "OnDebloque"

    def handle_expect_100(self) -> bool:
        if self.command in {"GET", "HEAD"}:
            return super().handle_expect_100()
        self.close_connection = True
        self.send_error(405)
        return False

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", self._csp)
        self.send_header("Cache-Control", self._cache)
        super().end_headers()

    def _bytes(
        self,
        code: int,
        body: bytes,
        content_type: str,
        retry: int | None = None,
        cache: str = "no-store",
        csp: str | None = None,
    ) -> None:
        self._cache = cache
        if csp:
            self._csp = csp
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if retry is not None:
            self.send_header("Retry-After", str(int(retry)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _wait_page(self, retry: int) -> None:
        self.close_connection = True
        page = (
            "<!DOCTYPE html>\n"
            '<html lang="fr">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '  <meta name="referrer" content="no-referrer">\n'
            '  <meta name="robots" content="noindex">\n'
            "  <title>Tu attendras</title>\n"
            f"  <style>{WAIT_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            '  <div class="bar"></div>\n'
            "  <main>\n"
            "    <h1>Tu attendras.</h1>\n"
            "    <p>Deux places. Pas de répartiteur de charge. "
            f"Réessaie dans {int(retry)} secondes.</p>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        ).encode("utf-8")
        self._bytes(429, page, "text/html; charset=utf-8", retry, csp=WAIT_CSP)

    def _serve_text(self, text: str, content_type: str) -> None:
        body = text.replace(PUBLIC_ORIGIN, site_origin()).encode("utf-8")
        self._bytes(200, body, content_type)

    def _serve_static(self, path: Path) -> None:
        body = path.read_bytes()
        cache = "no-store" if path.name in {"fiches.json", "submissions.json", "comparaison.json"} else "public, max-age=3600"
        self._bytes(200, body, _TYPES[path.suffix.lower()], cache=cache)

    def _page(self) -> None:
        snap = visit_snapshot()
        if countable(self.command, self.headers):
            try:
                snap = record_visit()
            except Exception as exc:
                print(f"compteur raté: {exc}", flush=True)
        shown = _fr_int(int(snap["total"]))
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        text = text.replace(
            '<strong id="visits">…</strong>',
            f'<strong id="visits" data-held="1">{shown}</strong>',
            1,
        )
        self._serve_text(text, "text/html; charset=utf-8")

    def _route(self) -> None:
        path = bare_path(self.path)
        if path == "/api/status":
            with _lock:
                body = json.dumps(_status, ensure_ascii=False).encode("utf-8")
            self._bytes(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/visits":
            body = json.dumps(visit_snapshot(), ensure_ascii=False).encode("utf-8")
            self._bytes(200, body, "application/json; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            self._page()
            return
        if path == "/robots.txt":
            origin = site_origin()
            text = (
                "User-agent: Twitterbot\n"
                "Disallow:\n"
                "\n"
                "User-agent: *\n"
                "Allow: /\n"
                "\n"
                f"Sitemap: {origin}/sitemap.xml\n"
            )
            self._bytes(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        if path == "/sitemap.xml":
            origin = site_origin()
            text = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"  <url><loc>{origin}/</loc></url>\n"
                "</urlset>\n"
            )
            self._bytes(200, text.encode("utf-8"), "application/xml; charset=utf-8")
            return
        local = public_file(path)
        if local is None:
            self.send_error(404)
            return
        self._serve_static(local)

    def _guarded(self, handler) -> None:
        self._csp = FRAME_CSP
        self._cache = "no-store"
        allowed, retry = _enter(self.client_address[0])
        if not allowed:
            self._wait_page(retry)
            return
        try:
            handler()
        finally:
            _leave()

    def _reject(self) -> None:
        self.close_connection = True
        allowed, retry = _enter(self.client_address[0])
        if not allowed:
            self._wait_page(retry)
            return
        try:
            self.send_error(405)
        finally:
            _leave()

    def do_GET(self) -> None:
        self._guarded(self._route)

    def do_HEAD(self) -> None:
        self._guarded(self._route)

    def __getattr__(self, name):
        if isinstance(name, str) and name.startswith("do_"):
            return self._reject
        raise AttributeError(name)

    def log_message(self, fmt: str, *args) -> None:
        code = str(args[1]) if len(args) >= 2 else ""
        if code in {"200", "304"}:
            return
        super().log_message(fmt, *args)


class SiteServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request, client_address) -> None:
        if not _slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 429 Too Many Requests\r\n"
                    b"Retry-After: 20\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n"
                    b"X-Content-Type-Options: nosniff\r\n"
                    b"\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            _slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            _slots.release()


def main() -> None:
    forced = os.environ.get("SITE_URL", "").strip()
    if forced and not _ORIGIN_RE.fullmatch(forced.rstrip("/")):
        print("SITE_URL ignorée, origine Pages conservée", flush=True)
    _seed_from_disk()
    threading.Thread(target=_loop, name="refresh", daemon=True).start()
    threading.Thread(target=_follower_loop, name="followers", daemon=True).start()
    server = SiteServer((HOST, PORT), Handler)
    print(
        f"site sur http://{HOST}:{PORT}  relevé toutes les {REFRESH_SECONDS}s  "
        f"abonnés toutes les {FOLLOWERS_SECONDS}s  "
        f"file {MAX_INFLIGHT} places, {MAX_QUEUE} en attente",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
