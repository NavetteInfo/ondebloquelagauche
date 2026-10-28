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


def safe_profile_url(raw: str) -> str | None:
    try:
        url = urlparse(raw)
    except Exception:
        return None
    if url.scheme != "https" or url.username or url.password:
        return None
    if url.port and url.port != 443:
        return None
    if url.netloc.lower() not in ALLOWED_PROFILE_HOSTS:
        return None
    return url.geturl()


class _PinHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl).hostname or ""
        if host != ALLOWED_HOST:
            raise urllib.error.URLError(f"redirection refusée vers {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    opener = urllib.request.build_opener(_PinHost)
    with opener.open(request, timeout=30) as response:
        final = urlparse(response.geturl()).hostname
        if final != ALLOWED_HOST:
            raise RuntimeError(f"hôte inattendu: {final}")
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
        name = clean(entry.get("name"))
        platform = str(entry.get("platform") or "").strip().lower()
        entry_id = str(entry.get("id") or "").strip().lower()
        if not name or platform not in PLATFORMS or not entry_id:
            continue
        url = safe_profile_url(entry.get("profile_url") or "")
        if not url:
            continue
        fiches.append(
            {
                "name": name,
                "category": clean(entry.get("category")) or "Autres",
                "platform": platform,
                "url": url,
                "reason": clean(entry.get("reason")),
                "count": int(counts.get(f"{platform}:{entry_id}") or 0),
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
        "total_blocks": int(counts_doc.get("total") or 0),
        "historical_base": int(counts_doc.get("historical_base") or 0),
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
