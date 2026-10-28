#!/usr/bin/env python3
"""Compteur de passages. Une ouverture de la page, pas les fichiers."""

from __future__ import annotations

import json
import os
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
