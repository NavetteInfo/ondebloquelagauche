#!/usr/bin/env python3
"""Compteur de passages. Une ouverture réelle de la page, pas les fichiers."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATH = Path(os.environ.get("VISITS_PATH", ROOT / "data" / "visits.json"))
KEEP_DAYS = 90

_lock = threading.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _blank() -> dict:
    return {"total": 0, "days": {}}


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    days = data.get("days")
    if not isinstance(days, dict):
        days = {}
    clean = {}
    for key, value in days.items():
        if isinstance(key, str) and isinstance(value, int) and value >= 0:
            clean[key] = value
    total = data.get("total")
    if not isinstance(total, int) or total < 0:
        total = sum(clean.values())
    return {"total": total, "days": clean}


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, PATH)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _prune(days: dict, today: str) -> None:
    cutoff = (datetime.fromisoformat(today) - timedelta(days=KEEP_DAYS)).date().isoformat()
    for key in list(days):
        if key < cutoff:
            del days[key]


# Bibliothèques, aperçus et robots. « cubot » est un téléphone, pas un robot.
_AUTO = (
    "headless",
    "phantom",
    "selenium",
    "puppeteer",
    "playwright",
    "wget",
    "curl/",
    "python-requests",
    "python-urllib",
    "aiohttp",
    "httpx/",
    "go-http-client",
    "okhttp",
    "libwww",
    "scrapy",
    "postman",
    "axios/",
    "node-fetch",
    "undici",
    "java/",
    "httpclient",
    "facebookexternalhit",
    "facebot",
    "twitterbot",
    "telegrambot",
    "slackbot",
    "discordbot",
    "linkedinbot",
    "embedly",
    "pinterestbot",
    "applebot",
    "bingbot",
    "googlebot",
    "google-inspectiontool",
    "adsbot-google",
    "mediapartners-google",
    "duckduckbot",
    "baiduspider",
    "yandexbot",
    "yandeximages",
    "ahrefs",
    "semrush",
    "mj12bot",
    "dotbot",
    "petalbot",
    "bytespider",
    "gptbot",
    "chatgpt-user",
    "oai-searchbot",
    "claudebot",
    "anthropic-ai",
    "amazonbot",
    "ccbot",
    "qwantify",
    "seznambot",
    "sogou",
    "exabot",
    "rogerbot",
    "uptimerobot",
    "pingdom",
    "statuscake",
    "ia_archiver",
    "archive.org_bot",
    "spider",
    "crawler",
    "slurp",
    "preview",
    "feedfetcher",
    "feedly",
)

_LANG = re.compile(r"[A-Za-z]{2,8}")
_SITES = {"none", "same-origin", "same-site", "cross-site"}


def _one(headers, name: str) -> str | None:
    """Une seule valeur, ou None si l'en-tête est répété."""
    if headers is None:
        return ""
    if hasattr(headers, "get_all"):
        values = headers.get_all(name) or []
    else:
        values = []
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                values.append(value)
    if not values:
        return ""
    if len(values) != 1:
        return None
    return " ".join(str(values[0]).split())


def _browser(ua: str) -> bool:
    if "Mozilla/5.0" not in ua:
        return False
    if "Firefox/" in ua or "FxiOS/" in ua:
        return True
    if any(token in ua for token in ("Chrome/", "CriOS/", "Edg/", "EdgiOS/", "OPR/", "OPiOS/", "SamsungBrowser/")):
        return "Safari/" in ua
    return "Version/" in ua and "Safari/" in ua and "Chrome/" not in ua


def _automated(ua: str) -> bool:
    folded = ua.casefold()
    if any(token in folded for token in _AUTO):
        return True
    return "bot" in folded.replace("cubot", "")


def credible(headers) -> bool:
    """Vrai pour une navigation de navigateur. Les en-têtes douteux restent dehors."""
    ua = _one(headers, "User-Agent")
    if ua is None or not 16 <= len(ua) <= 400:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in ua):
        return False
    if not _browser(ua) or _automated(ua):
        return False
    accept = _one(headers, "Accept")
    if accept is None or "text/html" not in accept.casefold():
        return False
    language = _one(headers, "Accept-Language")
    if language is None or not _LANG.search(language):
        return False
    purpose = " ".join(
        _one(headers, name) or ""
        for name in ("Sec-Purpose", "Purpose", "X-Purpose", "X-Moz")
    ).casefold()
    if "prefetch" in purpose or "preview" in purpose:
        return False
    mode = _one(headers, "Sec-Fetch-Mode")
    dest = _one(headers, "Sec-Fetch-Dest")
    if not mode or not dest or mode.casefold() != "navigate" or dest.casefold() != "document":
        return False
    site = _one(headers, "Sec-Fetch-Site")
    if site is None or (site != "" and site.casefold() not in _SITES):
        return False
    fetch_user = _one(headers, "Sec-Fetch-User")
    if fetch_user is None or fetch_user not in ("", "?1"):
        return False
    return True


def countable(method: str, headers) -> bool:
    return method == "GET" and credible(headers)


def record_visit() -> dict:
    with _lock:
        data = _load()
        today = _today()
        data["total"] += 1
        data["days"][today] = int(data["days"].get(today) or 0) + 1
        _prune(data["days"], today)
        _save(data)
        return _public(data, today)


def snapshot() -> dict:
    with _lock:
        return _public(_load(), _today())


def _public(data: dict, today: str) -> dict:
    return {
        "total": int(data["total"]),
        "today": int(data["days"].get(today) or 0),
    }
