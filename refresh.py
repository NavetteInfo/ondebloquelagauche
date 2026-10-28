#!/usr/bin/env python3
"""Télécharge api/counts et api/accounts, puis écrit data/fiches.json."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
FICHES = DATA / "fiches.json"

COUNTS_URL = "https://onbloquelextremedroite.fun/api/counts"
ACCOUNTS_URL = "https://onbloquelextremedroite.fun/api/accounts"
ALLOWED_HOST = "onbloquelextremedroite.fun"
# Cloudflare refuse un client sans tête de navigateur. L'API reste publique.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
    "OnDebloqueLaGauche/1.0"
)
MAX_BYTES = 8_000_000
ALLOWED_PROFILE_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "instagram.com",
    "www.instagram.com",
    "facebook.com",
    "www.facebook.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "tiktok.com",
    "www.tiktok.com",
}
PLATFORMS = {"x", "instagram", "facebook", "youtube", "tiktok"}


def clean(value: str) -> str:
    text = str(value or "")
    text = text.replace("\u2014", " : ").replace("\u2013", "-").replace("\u2212", "-")
    text = text.replace(" · ", " - ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+:", " :", text)
    return text


_PROFILE_RE = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,400}\Z")
_COUNT_CAP = 10**15


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0 or value > _COUNT_CAP:
        return 0
    return value


def _clip(value, limit: int) -> str:
    return clean(value)[:limit]


def safe_profile_url(raw: str) -> str | None:
    if not isinstance(raw, str) or not _PROFILE_RE.fullmatch(raw):
        return None
    if re.search(r"%0[0adAD]|%5[cC]", raw):
        return None
    try:
        url = urlparse(raw)
    except Exception:
        return None
    host = (url.hostname or "").lower()
    if url.scheme != "https" or url.username or url.password:
        return None
    if url.port not in (None, 443):
        return None
    if not host or host.endswith(".") or host not in ALLOWED_PROFILE_HOSTS:
        return None
    query = f"?{url.query}" if url.query else ""
    return f"https://{host}{url.path or '/'}{query}"


def _same_source(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and host == ALLOWED_HOST
        and not host.endswith(".")
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
    )


class _PinHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _same_source(newurl):
            raise urllib.error.URLError("redirection refusée")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    opener = urllib.request.build_opener(_PinHost)
    with opener.open(request, timeout=30) as response:
        if not _same_source(response.geturl()):
            raise RuntimeError("hôte inattendu")
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise RuntimeError("réponse trop grosse")
    if payload.lstrip().startswith(b"<"):
        raise RuntimeError("la source a renvoyé une page, pas du JSON")
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("JSON inattendu")
    return data


def merge(accounts: dict, counts_doc: dict, refreshed_at: str | None = None) -> dict:
    entries = accounts.get("entries")
    counts = counts_doc.get("counts")
    if not isinstance(entries, list) or not isinstance(counts, dict):
        raise RuntimeError("fichiers incomplets")

    fiches = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") == "removal":
            continue
        name = _clip(entry.get("name"), 180)
        platform = str(entry.get("platform") or "").strip().lower()
        entry_id = str(entry.get("id") or "").strip().lower()
        if not name or platform not in PLATFORMS or not entry_id or len(entry_id) > 200:
            continue
        if any(ord(char) < 32 for char in entry_id):
            continue
        url = safe_profile_url(entry.get("profile_url") or "")
        if not url:
            continue
        fiches.append(
            {
                "name": name,
                "category": _clip(entry.get("category"), 80) or "Autres",
                "platform": platform,
                "url": url,
                "reason": _clip(entry.get("reason"), 400),
                "count": _count(counts.get(f"{platform}:{entry_id}")),
            }
        )

    fiches.sort(key=lambda fiche: (-fiche["count"], fiche["name"].casefold()))
    categories = Counter(fiche["category"] for fiche in fiches)
    platforms = Counter(fiche["platform"] for fiche in fiches)
    return {
        "source": "api/accounts + api/counts",
        "snapshot_at": accounts.get("snapshot_at"),
        "as_of": counts_doc.get("as_of"),
        "refreshed_at": refreshed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_blocks": _count(counts_doc.get("total")),
        "historical_base": _count(counts_doc.get("historical_base")),
        "live": bool(counts_doc.get("live")),
        "fiche_count": len(fiches),
        "name_count": len({fiche["name"].casefold() for fiche in fiches}),
        "fiche_count_sum": sum(fiche["count"] for fiche in fiches),
        "categories": categories.most_common(),
        "platforms": platforms.most_common(),
        "fiches": fiches,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def refresh_once() -> dict:
    counts_doc = fetch_json(COUNTS_URL)
    accounts = fetch_json(ACCOUNTS_URL)
    refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged = merge(accounts, counts_doc, refreshed_at)
    RAW.mkdir(parents=True, exist_ok=True)
    _write_json(RAW / "counts.json", counts_doc)
    _write_json(RAW / "accounts.json", accounts)
    _write_json(FICHES, merged)
    return merged


def main() -> None:
    merged = refresh_once()
    print(
        f"ok fiches={merged['fiche_count']} total={merged['total_blocks']} "
        f"as_of={merged['as_of']}"
    )


if __name__ == "__main__":
    main()
