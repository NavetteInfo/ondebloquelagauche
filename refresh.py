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
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
FICHES = DATA / "fiches.json"
ABONNES = DATA / "abonnes.json"
# Sortie publique du dispositif, mardi 27 octobre 2026.
LAUNCH_DAY = "2026-10-27"
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?\Z")
_FOLLOWER_KEY_RE = re.compile(r"[a-z]+:/[a-z0-9._~:@/-]{1,200}\Z")
_FOLLOWER_FIELDS = (
    "followers_before",
    "followers_after",
    "followers_gained",
    "followers_from",
    "followers_to",
    "followers_rounded",
)

COUNTS_URL = "https://onbloquelextremedroite.fun/api/counts"
ACCOUNTS_URL = "https://onbloquelextremedroite.fun/api/accounts"
SUBMISSIONS_URL = "https://onbloquelextremedroite.fun/api/submissions"
SUBMISSIONS = DATA / "submissions.json"
ALLOWED_HOST = "onbloquelextremedroite.fun"
# Cloudflare refuse un client sans tête de navigateur. L'API reste publique.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
    "OnDebloqueLaGauche/1.0"
)
# La page publique envoie cet en-tête sur ses lectures. En recovery, sans lui,
# /api/counts et /api/accounts répondent 404 « Page introuvable. »
CLIENT_HEADER = "1"
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
_HOST_NET = {
    "x.com": "x",
    "www.x.com": "x",
    "twitter.com": "x",
    "www.twitter.com": "x",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
}


def clean(value: str) -> str:
    text = str(value or "")
    text = text.replace("\u2014", " : ").replace("\u2013", "-").replace("\u2212", "-")
    text = text.replace(" · ", " - ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+:", " :", text)
    return text


_PROFILE_RE = re.compile(r"https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,400}\Z")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
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


def profile_key(url: str) -> str | None:
    """Même identité que le relevé public : réseau:/chemin, sans la requête."""
    safe = safe_profile_url(url if isinstance(url, str) else "")
    if not safe:
        return None
    parsed = urlparse(safe)
    net = _HOST_NET.get((parsed.hostname or "").lower())
    if not net:
        return None
    path = (parsed.path or "/").lower()
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if net == "facebook" and path.endswith("/profile.php"):
        ident = (parse_qs(parsed.query).get("id") or [""])[0]
        if ident.isdigit():
            path = f"/id:{ident}"
    key = f"{net}:{path}"
    if not _FOLLOWER_KEY_RE.fullmatch(key) or ".." in key:
        return None
    return key


def _rounded_thousand(value: int) -> bool:
    return value % 1000 == 0


def _pos_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 < value <= _COUNT_CAP:
        return None
    return value


def _follower_hit(entry: dict) -> dict | None:
    """Compte public, et écart seulement si les deux relevés ne sont pas arrondis."""
    after = _pos_int(entry.get("after"))
    end = entry.get("to")
    if after is None or not isinstance(end, str) or not _DAY_RE.fullmatch(end):
        return None
    hit = {"followers_after": after, "followers_to": end}
    if _rounded_thousand(after):
        hit["followers_rounded"] = True
    before = entry.get("before")
    start = entry.get("from")
    if before is None:
        return hit
    before_n = _pos_int(before)
    if before_n is None or not isinstance(start, str) or not _STAMP_RE.fullmatch(start):
        return hit
    if _rounded_thousand(before_n) or _rounded_thousand(after):
        return hit
    gained = after - before_n
    if abs(gained) > _COUNT_CAP:
        return hit
    hit["followers_before"] = before_n
    hit["followers_gained"] = gained
    hit["followers_from"] = start
    return hit


def _follower_table() -> tuple[dict[str, dict], dict]:
    """data/abonnes.json est un extrait figé de followers.js. Pas de retéléchargement."""
    meta = {"baseline": "", "latest": "", "source": "followers.js"}
    if not ABONNES.is_file():
        return {}, meta
    try:
        payload = json.loads(ABONNES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, meta
    if not isinstance(payload, dict):
        return {}, meta
    for field in ("baseline", "latest"):
        value = payload.get(field)
        if isinstance(value, str) and _STAMP_RE.fullmatch(value):
            meta[field] = value
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {}, meta
    table: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if (
            not isinstance(key, str)
            or not _FOLLOWER_KEY_RE.fullmatch(key)
            or ".." in key
        ):
            continue
        hit = _follower_hit(entry)
        if hit:
            table[key] = hit
    return table, meta


def attach_followers(doc: dict) -> None:
    """Pose les abonnés. Ces chiffres ne comptent pas dans les blocages."""
    table, meta = _follower_table()
    fiches = doc.get("fiches")
    if not isinstance(fiches, list):
        return
    matched = 0
    compared = 0
    for fiche in fiches:
        if not isinstance(fiche, dict):
            continue
        for field in _FOLLOWER_FIELDS:
            fiche.pop(field, None)
        hit = table.get(profile_key(str(fiche.get("url") or "")))
        if not hit:
            continue
        fiche.update(hit)
        matched += 1
        if "followers_gained" in hit:
            compared += 1
    doc["followers"] = {
        "baseline": meta["baseline"] or None,
        "latest": meta["latest"] or None,
        "matched": matched,
        "compared": compared,
        "source": "followers.js",
    }


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


def _read_source(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-BF": CLIENT_HEADER,
        },
    )
    opener = urllib.request.build_opener(_PinHost)
    with opener.open(request, timeout=60) as response:
        if not _same_source(response.geturl()):
            raise RuntimeError("hôte inattendu")
        status = int(getattr(response, "status", 200))
        payload = response.read(MAX_BYTES + 1)
    return status, payload


def fetch_json(url: str) -> dict:
    """Deux essais. Le second ne sert que si la lecture cale, pas si le JSON est faux."""
    last = None
    for attempt in range(2):
        try:
            _status, payload = _read_source(url)
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt:
                raise
            last = exc
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            last = exc
            if attempt:
                raise
    else:
        raise last
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


def _public_text(payload: bytes) -> str:
    text = payload.decode("utf-8", "replace").strip()
    if not text:
        return "réponse vide"
    if text[:1] == "<" or "<html" in text[:240].lower():
        return "la source a renvoyé une page, pas du JSON"
    return _clip(text, 180)


def _submission_rows(data) -> tuple[list[dict], int]:
    rows = data if isinstance(data, list) else None
    if rows is None and isinstance(data, dict):
        for key in ("submissions", "entries", "items", "proposals"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
    if rows is None:
        return [], 0
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _clip(row.get("name"), 180)
        url = safe_profile_url(row.get("profile_url") or row.get("url") or "")
        if not name and not url:
            continue
        platform = str(row.get("platform") or "").strip().lower()
        kind = str(row.get("type") or "").strip().lower()
        items.append(
            {
                "name": name,
                "platform": platform if platform in PLATFORMS else "",
                "url": url or "",
                "type": kind if kind in {"new", "removal", "update"} else "",
                "reason": _clip(row.get("reason"), 400),
            }
        )
        if len(items) >= 40:
            break
    return items, len(rows)


def _published_rows(accounts: dict | None) -> list[dict]:
    """Ce que /api/accounts a déjà versé : ajouts non vérifiés, mises à jour, retraits.

    Le GET /api/submissions ne publie pas cette file. Pendant la maintenance il
    répond 404. Ces lignes sont la trace publique, pas le contenu du guichet fermé.
    """
    if not isinstance(accounts, dict) or not isinstance(accounts.get("entries"), list):
        return []
    picked = []
    hidden_removals = 0
    for entry in accounts["entries"]:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").strip().lower()
        if kind == "removal":
            target = str(entry.get("target_id") or "").strip()
            if not target or any(ord(char) < 32 for char in target) or len(target) > 80:
                continue
            if _UUID_RE.fullmatch(target):
                hidden_removals += 1
                continue
            picked.append(
                {
                    "name": target,
                    "platform": "",
                    "profile_url": "",
                    "type": "removal",
                    "reason": "Retrait déjà versé dans la liste.",
                }
            )
            continue
        # L'API envoie 1, pas le booléen true.
        if kind not in {"new", "update"} or entry.get("verified") in (True, 1):
            continue
        row = {
            "name": entry.get("name"),
            "platform": entry.get("platform"),
            "profile_url": entry.get("profile_url") or entry.get("url") or "",
            "type": kind,
            "reason": entry.get("reason"),
        }
        if not str(row["reason"] or "").strip():
            row["reason"] = (
                "Ajout publié, pas vérifié à la main."
                if kind == "new"
                else "Mise à jour publiée, pas vérifiée à la main."
            )
        picked.append(row)
    if hidden_removals:
        label = "retrait sans nom public" if hidden_removals == 1 else "retraits sans nom public"
        picked.append(
            {
                "name": f"{hidden_removals} {label}",
                "platform": "",
                "profile_url": "",
                "type": "removal",
                "reason": "Identifiants déjà retirés de la liste.",
            }
        )
    items, _total = _submission_rows(picked)
    return items


def pull_submissions(accounts: dict | None = None, pulled_at: str | None = None) -> dict:
    """Lecture seule. Aucune proposition n'est envoyée."""
    doc = {
        "source": "api/submissions",
        "ok": False,
        "http_status": 0,
        "message": "lecture ratée",
        "pulled_at": pulled_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": 0,
        "items": [],
    }
    try:
        status, payload = _read_source(SUBMISSIONS_URL)
    except urllib.error.HTTPError as exc:
        doc["http_status"] = int(exc.code)
        try:
            payload = exc.read(8192)
        except Exception:
            payload = b""
        doc["message"] = _public_text(payload)
        _fill_published(doc, accounts)
        _write_json(SUBMISSIONS, doc)
        return doc
    except Exception:
        _fill_published(doc, accounts)
        _write_json(SUBMISSIONS, doc)
        return doc
    doc["http_status"] = status
    if len(payload) > MAX_BYTES or payload.lstrip().startswith(b"<"):
        doc["message"] = "la source a renvoyé une page, pas du JSON"
        _fill_published(doc, accounts)
        _write_json(SUBMISSIONS, doc)
        return doc
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        doc["message"] = "JSON inattendu"
        _fill_published(doc, accounts)
        _write_json(SUBMISSIONS, doc)
        return doc
    items, total = _submission_rows(data)
    doc["ok"] = True
    doc["message"] = ""
    doc["total"] = total
    doc["items"] = items
    _write_json(SUBMISSIONS, doc)
    return doc


def _fill_published(doc: dict, accounts: dict | None) -> None:
    items = _published_rows(accounts)
    if not items:
        return
    doc["via"] = "api/accounts"
    doc["total"] = len(items)
    doc["items"] = items


def refresh_once() -> dict:
    counts_doc = fetch_json(COUNTS_URL)
    accounts = fetch_json(ACCOUNTS_URL)
    refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged = merge(accounts, counts_doc, refreshed_at)
    attach_followers(merged)
    RAW.mkdir(parents=True, exist_ok=True)
    _write_json(RAW / "counts.json", counts_doc)
    _write_json(RAW / "accounts.json", accounts)
    _write_json(FICHES, merged)
    pull_submissions(accounts, refreshed_at)
    return merged


def main() -> None:
    merged = refresh_once()
    print(
        f"ok fiches={merged['fiche_count']} total={merged['total_blocks']} "
        f"as_of={merged['as_of']}"
    )


if __name__ == "__main__":
    main()
