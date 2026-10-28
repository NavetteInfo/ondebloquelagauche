#!/usr/bin/env python3
"""Compare les pulls d'accounts et de counts, et écrit data/comparaison.json.

Les JSON datés vivent dans data/historique. Le dossier externe, s'il existe,
est PULLS_DIR, sinon /pulls, sinon le dossier pulls à côté du dépôt. Les deux
sont lus. Sans aucune copie, le fichier déjà publié n'est pas écrasé. La
comparaison ne publie que les écarts, les totaux, et les plus fortes hausses.
Un recul d'un ou deux points qui revient ensuite est compté, le nom n'est pas
publié. Un recul d'au moins 10, ou un solde encore négatif à la fin, est nommé.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from refresh import clean, safe_profile_url

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "comparaison.json"
PARIS = ZoneInfo("Europe/Paris")
MAX_BYTES = 8_000_000
EDIT_CAP = 80
DOWN_CAP = 40
GAIN_CAP = 8
STEP_CAP = 500
NOTABLE_DROP = 10
HAND = (
    "name",
    "category",
    "reason",
    "profile_url",
    "zone",
    "verified",
    "type",
    "target_id",
)
FIELD_LABEL = {
    "name": "nom",
    "category": "rayon",
    "reason": "motif",
    "profile_url": "lien",
    "zone": "zone",
    "verified": "vérifié",
    "type": "type",
    "target_id": "cible",
    "lignes": "lignes",
}
SHORT_MONTHS = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)
_FILE_RE = re.compile(
    r"^(accounts|counts)_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_(\d+))?\.json$"
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+|00)\d[\d\s().-]{6,}\d|\b0\d(?:[\s.-]?\d{2}){4}\b")
_BIDI_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_UTC_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$")
_HANDLE_RE = re.compile(r"[A-Za-z0-9._-]{1,40}\Z")


def locate_pulls() -> Path | None:
    """PULLS_DIR s'il est posé, sinon /pulls, sinon le frère du dépôt."""
    env = os.environ.get("PULLS_DIR", "").strip()
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    for candidate in (Path("/pulls"), ROOT.parent / "pulls"):
        if candidate.is_dir():
            return candidate
    return None


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 10**15:
        return None
    return value


def public_text(value, limit: int) -> str:
    text = clean(value if isinstance(value, str) else "" if value is None else str(value))
    text = _BIDI_RE.sub("", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = _EMAIL_RE.sub("", text)
    text = _PHONE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -:,;")
    return text[:limit]


def _clock(moment: datetime) -> dict:
    utc = moment.astimezone(timezone.utc).replace(microsecond=0)
    local = moment.astimezone(PARIS)
    month = SHORT_MONTHS[local.month - 1]
    return {
        "at": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local": f"{local.day} {month} {local:%H:%M}",
        "utc": f"{utc:%H:%M}",
    }


def _parse_utc(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _UTC_RE.fullmatch(value.strip())
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _stamp_from_name(name: str) -> tuple[str, datetime, int] | None:
    match = _FILE_RE.fullmatch(name)
    if not match:
        return None
    kind = match.group(1)
    try:
        local = datetime(
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
            int(match.group(7)),
            tzinfo=PARIS,
        )
        seq = int(match.group(8) or 0)
    except ValueError:
        return None
    return kind, local, seq


def _load_json(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _entry_key(entry: dict) -> str | None:
    platform = entry.get("platform")
    ident = entry.get("id")
    if not isinstance(platform, str) or not isinstance(ident, str):
        return None
    platform = platform.strip().lower()
    ident = ident.strip().lower()
    if not platform or not ident or ":" in platform:
        return None
    return f"{platform}:{ident}"


def _norm_field(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    number = _as_int(value)
    if number is not None:
        return str(number)
    if not isinstance(value, str):
        return ""
    return public_text(value, 500)


def _row_tuple(row: dict) -> tuple[str, ...]:
    return tuple(_norm_field(row.get(field)) for field in HAND)


def _signature(rows: list[dict]) -> tuple:
    return tuple(sorted(_row_tuple(row) for row in rows))


def _index_accounts(doc: dict) -> tuple[dict[str, list[dict]], int, int]:
    entries = doc.get("entries")
    grouped: dict[str, list[dict]] = {}
    blank = 0
    lines = 0
    if not isinstance(entries, list):
        return grouped, blank, lines
    lines = len(entries)
    for entry in entries:
        if not isinstance(entry, dict):
            blank += 1
            continue
        key = _entry_key(entry)
        if not key:
            blank += 1
            continue
        grouped.setdefault(key, []).append(entry)
    return grouped, blank, lines


def _accounts_digest(doc: dict) -> str:
    grouped, blank, lines = _index_accounts(doc)
    frozen = doc.get("frozen")
    snapshot = doc.get("snapshot_at") if isinstance(doc.get("snapshot_at"), str) else ""
    rejected = doc.get("rejected_removals")
    rejected_n = len(rejected) if isinstance(rejected, list) else None
    body = tuple(sorted((key, _signature(rows)) for key, rows in grouped.items()))
    payload = json.dumps(
        [lines, blank, frozen is True, snapshot, rejected_n, body],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _meta(doc: dict) -> dict:
    rejected = doc.get("rejected_removals")
    snapshot = doc.get("snapshot_at")
    return {
        "snapshot_at": snapshot if isinstance(snapshot, str) else "",
        "frozen": doc.get("frozen") if isinstance(doc.get("frozen"), bool) else None,
        "rejected": len(rejected) if isinstance(rejected, list) else None,
    }


def _counts_map(doc: dict) -> dict[str, int]:
    raw = doc.get("counts")
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        number = _as_int(value)
        if isinstance(key, str) and number is not None:
            counts[key] = number
    return counts


def _show_change(field: str, old: str, new: str) -> dict:
    label = FIELD_LABEL.get(field, field)
    if field == "profile_url":
        before = safe_profile_url(old) or ""
        after = safe_profile_url(new) or ""
        return {
            "field": label,
            "before": before,
            "after": after,
            "hidden": not before and not after,
        }
    if field == "target_id" or _UUID_RE.fullmatch(old) or _UUID_RE.fullmatch(new):
        return {"field": label, "before": "", "after": "", "hidden": True}
    return {"field": label, "before": old, "after": new, "hidden": False}


def _field_changes(before: list[dict], after: list[dict]) -> list[dict]:
    left = sorted(before, key=_row_tuple)
    right = sorted(after, key=_row_tuple)
    if len(left) != len(right):
        return [_show_change("lignes", str(len(left)), str(len(right)))]
    changes = []
    for old_row, new_row in zip(left, right):
        old_values = _row_tuple(old_row)
        new_values = _row_tuple(new_row)
        for field, old, new in zip(HAND, old_values, new_values):
            if old != new:
                changes.append(_show_change(field, old, new))
    return changes


def _card(row: dict) -> dict:
    platform = row.get("platform")
    platform = platform.strip().lower() if isinstance(platform, str) else ""
    card = {
        "name": public_text(row.get("name"), 160) or "Sans nom",
        "platform": platform if re.fullmatch(r"[a-z]{1,20}", platform) else "",
    }
    category = public_text(row.get("category"), 80)
    if category:
        card["category"] = category
    url = row.get("profile_url")
    safe = safe_profile_url(url if isinstance(url, str) else "")
    if safe:
        card["url"] = safe
    reason = public_text(row.get("reason"), 400)
    if reason:
        card["reason"] = reason
    return card


def _remember(names: dict[str, str], urls: dict[str, str], grouped: dict[str, list[dict]]) -> None:
    for key, rows in grouped.items():
        for row in rows:
            label = public_text(row.get("name"), 160)
            if label:
                names[key] = label
            url = row.get("profile_url")
            safe = safe_profile_url(url if isinstance(url, str) else "")
            if safe:
                urls[key] = safe


def _count_label(key: str, names: dict[str, str]) -> tuple[str, str]:
    platform, _, ident = key.partition(":")
    if key in names:
        return names[key], platform
    if _UUID_RE.fullmatch(ident) or not _HANDLE_RE.fullmatch(ident):
        return "Compteur sans fiche", platform
    return ident, platform


def _journal(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        lines = path.read_bytes().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    http_404 = sum(1 for line in lines if b"404" in line)
    return {"lines": len(lines), "http_404": http_404, "other": len(lines) - http_404}


def _discover(folder: Path) -> list[dict]:
    found: dict[tuple[str, int], dict] = {}
    roots = [folder]
    for kind in ("accounts", "counts"):
        nested = folder / kind
        if nested.is_dir():
            roots.append(nested)
    seen: set[Path] = set()
    for root in roots:
        try:
            paths = list(root.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            parsed = _stamp_from_name(path.name)
            if not parsed:
                continue
            kind, local, seq = parsed
            clock = _clock(local)
            slot = found.setdefault(
                (clock["at"], seq),
                {"clock": clock, "source": "archive", "seq": seq},
            )
            slot[kind] = path
    return [found[key] for key in sorted(found)]


def _site_moment(data_dir: Path) -> datetime | None:
    fiches = _load_json(data_dir / "fiches.json")
    if fiches:
        parsed = _parse_utc(fiches.get("refreshed_at"))
        if parsed:
            return parsed
    counts = _load_json(data_dir / "raw" / "counts.json")
    if counts:
        parsed = _parse_utc(counts.get("as_of"))
        if parsed:
            return parsed.replace(microsecond=0)
    for name in ("accounts.json", "counts.json"):
        path = data_dir / "raw" / name
        try:
            if path.is_file() and not path.is_symlink():
                return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0)
        except OSError:
            continue
    return None


def _append_site(slots: list[dict], data_dir: Path) -> None:
    accounts_path = data_dir / "raw" / "accounts.json"
    counts_path = data_dir / "raw" / "counts.json"
    has_accounts = accounts_path.is_file() and not accounts_path.is_symlink()
    has_counts = counts_path.is_file() and not counts_path.is_symlink()
    if not has_accounts and not has_counts:
        return
    moment = _site_moment(data_dir)
    if moment is None:
        return
    accounts_doc = _load_json(accounts_path) if has_accounts else None
    counts_doc = _load_json(counts_path) if has_counts else None
    if accounts_doc is None and counts_doc is None:
        return
    last_accounts = None
    last_counts = None
    last_at = ""
    for slot in slots:
        if "accounts" in slot:
            last_accounts = slot["accounts"]
            last_at = slot["clock"]["at"]
        if "counts" in slot:
            last_counts = slot["counts"]
            last_at = max(last_at, slot["clock"]["at"])
    newer = not last_at or _clock(moment)["at"] > last_at
    accounts_differ = False
    counts_differ = False
    if accounts_doc is not None:
        previous = _load_json(last_accounts) if isinstance(last_accounts, Path) else None
        accounts_differ = previous is None or _accounts_digest(previous) != _accounts_digest(accounts_doc)
    if counts_doc is not None:
        previous = _load_json(last_counts) if isinstance(last_counts, Path) else None
        if previous is None:
            counts_differ = True
        else:
            counts_differ = _counts_map(previous) != _counts_map(counts_doc) or _as_int(
                previous.get("total")
            ) != _as_int(counts_doc.get("total"))
    if not newer and not accounts_differ and not counts_differ:
        return
    slot = {"clock": _clock(moment), "source": "site"}
    if accounts_doc is not None:
        slot["accounts_doc"] = accounts_doc
    if counts_doc is not None:
        slot["counts_doc"] = counts_doc
    slots.append(slot)
    slots.sort(key=lambda item: (item["clock"]["at"], 1 if item["source"] == "site" else 0))


def _trim_steps(slots: list[dict]) -> tuple[list[dict], int]:
    if len(slots) <= STEP_CAP:
        return slots, 0
    omitted = len(slots) - STEP_CAP
    return slots[-STEP_CAP:], omitted


def _history_folders(folder: Path | None) -> list[Path]:
    folders: list[Path] = []
    history = ROOT / "data" / "historique"
    if history.is_dir():
        folders.append(history)
    if folder is not None and folder.is_dir():
        try:
            same = folders and folder.resolve() == folders[0].resolve()
        except OSError:
            same = False
        if not same:
            folders.append(folder)
    return folders


def _merge_slots(folders: list[Path]) -> list[dict]:
    found: dict[tuple[str, int], dict] = {}
    for folder in folders:
        for slot in _discover(folder):
            key = (slot["clock"]["at"], int(slot.get("seq") or 0))
            current = found.get(key)
            if current is None:
                found[key] = slot
                continue
            for kind in ("accounts", "counts"):
                if kind in slot and kind not in current:
                    current[kind] = slot[kind]
    return [found[key] for key in sorted(found)]


def build(folder: Path, data_dir: Path | None) -> dict | None:
    folders = _history_folders(folder)
    slots = _merge_slots(folders)
    if data_dir is not None:
        _append_site(slots, data_dir)
    if not slots:
        return None
    slots, omitted = _trim_steps(slots)
    names: dict[str, str] = {}
    urls: dict[str, str] = {}
    edits: list[dict] = []
    steps: list[dict] = []
    notable: list[tuple[int, dict]] = []
    dip_count = 0
    dip_max = 0
    prev_rows: dict[str, list[dict]] | None = None
    prev_digest: str | None = None
    prev_lines: int | None = None
    prev_counts: dict[str, int] | None = None
    prev_total: int | None = None
    prev_had_counts = False
    first_counts: dict[str, int] | None = None
    first_total: int | None = None
    last_counts: dict[str, int] | None = None
    last_total: int | None = None
    snapshots: list[str] = []
    frozen_values: list[bool] = []
    rejected_values: list[int] = []

    for slot in slots:
        clock = slot["clock"]
        step = {
            "at": clock["at"],
            "local": clock["local"],
            "utc": clock["utc"],
            "source": slot["source"],
            "accounts": None,
            "accounts_same": False,
            "added": 0,
            "removed": 0,
            "rewritten": 0,
            "counts_absent": True,
            "total": None,
            "total_delta": None,
            "down_count": 0,
            "keys_added": 0,
            "keys_removed": 0,
        }
        accounts_doc = slot.get("accounts_doc")
        if accounts_doc is None and isinstance(slot.get("accounts"), Path):
            accounts_doc = _load_json(slot["accounts"])
        if accounts_doc is not None:
            grouped, blank, lines = _index_accounts(accounts_doc)
            digest = _accounts_digest(accounts_doc)
            meta = _meta(accounts_doc)
            step["accounts"] = lines
            step["blank"] = blank
            if meta["snapshot_at"]:
                step["snapshot_at"] = meta["snapshot_at"]
                snapshots.append(meta["snapshot_at"])
            if meta["frozen"] is not None:
                step["frozen"] = meta["frozen"]
                frozen_values.append(meta["frozen"])
            if meta["rejected"] is not None:
                step["rejected"] = meta["rejected"]
                rejected_values.append(meta["rejected"])
            _remember(names, urls, grouped)
            if prev_digest is None:
                step["accounts_same"] = False
                step["accounts_first"] = True
            elif digest == prev_digest:
                step["accounts_same"] = True
            else:
                previous = prev_rows or {}
                added = sorted(set(grouped) - set(previous))
                removed = sorted(set(previous) - set(grouped))
                step_edits: list[dict] = []
                for key in sorted(set(grouped) & set(previous)):
                    if _signature(previous[key]) == _signature(grouped[key]):
                        continue
                    changes = _field_changes(previous[key], grouped[key])
                    if not changes:
                        continue
                    card = _card(grouped[key][-1])
                    card.pop("reason", None)
                    card.update(
                        kind="rewrite",
                        local=clock["local"],
                        source=slot["source"],
                        changes=changes,
                    )
                    step_edits.append(card)
                for key in removed:
                    card = _card(previous[key][-1])
                    card.update(kind="removal", local=clock["local"], source=slot["source"])
                    step_edits.append(card)
                for key in added:
                    card = _card(grouped[key][-1])
                    card.update(kind="addition", local=clock["local"], source=slot["source"])
                    step_edits.append(card)
                step_edits.sort(key=lambda card: (0 if card["kind"] == "rewrite" else 1 if card["kind"] == "removal" else 2, card.get("name", "").casefold()))
                edits.extend(step_edits)
                rewritten = sum(1 for card in step_edits if card["kind"] == "rewrite")
                step["added"] = len(added)
                step["removed"] = len(removed)
                step["rewritten"] = rewritten
                if prev_lines is not None and lines != prev_lines:
                    step["lines_delta"] = lines - prev_lines
            prev_rows = grouped
            prev_digest = digest
            prev_lines = lines

        counts_doc = slot.get("counts_doc")
        if counts_doc is None and isinstance(slot.get("counts"), Path):
            counts_doc = _load_json(slot["counts"])
        if counts_doc is not None:
            counts = _counts_map(counts_doc)
            total = _as_int(counts_doc.get("total"))
            step["counts_absent"] = False
            step["total"] = total
            as_of = counts_doc.get("as_of")
            if isinstance(as_of, str):
                step["as_of"] = as_of
            if prev_counts is not None:
                if not prev_had_counts:
                    step["counts_gap"] = True
                added_keys = set(counts) - set(prev_counts)
                removed_keys = set(prev_counts) - set(counts)
                ups = 0
                down_count = 0
                for key in set(counts) & set(prev_counts):
                    delta = counts[key] - prev_counts[key]
                    if delta > 0:
                        ups += 1
                    elif delta < 0:
                        down_count += 1
                        drop = -delta
                        if drop >= NOTABLE_DROP:
                            label, platform = _count_label(key, names)
                            record = {
                                "name": label,
                                "platform": platform,
                                "before": prev_counts[key],
                                "after": counts[key],
                                "delta": drop,
                                "local": clock["local"],
                            }
                            url = urls.get(key)
                            if url:
                                record["url"] = url
                            notable.append((drop, record))
                        else:
                            dip_count += 1
                            dip_max = max(dip_max, drop)
                step["keys_added"] = len(added_keys)
                step["keys_removed"] = len(removed_keys)
                step["ups"] = ups
                step["down_count"] = down_count
                if total is not None and prev_total is not None:
                    step["total_delta"] = total - prev_total
            if first_counts is None:
                first_counts = counts
                first_total = total
            last_counts = counts
            last_total = total
            prev_counts = counts
            prev_total = total
            prev_had_counts = True
        else:
            prev_had_counts = False
        steps.append(step)

    net_downs = 0
    if first_counts is not None and last_counts is not None:
        for key in set(first_counts) & set(last_counts):
            if last_counts[key] >= first_counts[key]:
                continue
            net_downs += 1
            drop = first_counts[key] - last_counts[key]
            label, platform = _count_label(key, names)
            record = {
                "name": label,
                "platform": platform,
                "before": first_counts[key],
                "after": last_counts[key],
                "delta": drop,
                "local": steps[-1]["local"],
                "span": True,
            }
            url = urls.get(key)
            if url:
                record["url"] = url
            notable.append((drop, record))

    notable.sort(key=lambda item: (-item[0], item[1]["name"]))
    seen_downs: set[tuple] = set()
    downs: list[dict] = []
    for _drop, record in notable:
        identity = (record["name"], record["platform"], record["before"], record["after"])
        if identity in seen_downs:
            continue
        seen_downs.add(identity)
        downs.append(record)
    hidden_downs = max(0, len(downs) - DOWN_CAP)
    downs = downs[:DOWN_CAP]

    gains: list[dict] = []
    keys_added = 0
    keys_removed = 0
    if first_counts is not None and last_counts is not None:
        keys_added = len(set(last_counts) - set(first_counts))
        keys_removed = len(set(first_counts) - set(last_counts))
        ranked = []
        for key in set(first_counts) & set(last_counts):
            delta = last_counts[key] - first_counts[key]
            if delta > 0:
                ranked.append((delta, key))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        for delta, key in ranked[:GAIN_CAP]:
            label, platform = _count_label(key, names)
            gain = {
                "name": label,
                "platform": platform,
                "before": first_counts[key],
                "after": last_counts[key],
                "delta": delta,
            }
            url = urls.get(key)
            if url:
                gain["url"] = url
            gains.append(gain)

    hidden_edits = max(0, len(edits) - EDIT_CAP)
    if len(edits) > EDIT_CAP:
        rewrites = [card for card in edits if card.get("kind") == "rewrite"][:EDIT_CAP]
        rest = [card for card in edits if card.get("kind") != "rewrite"]
        room = EDIT_CAP - len(rewrites)
        edits = rewrites + (rest[-room:] if room else [])
    else:
        edits = edits[:EDIT_CAP]
    snapshot_stable = None if not snapshots else len(set(snapshots)) <= 1
    frozen_stable = len(set(frozen_values)) <= 1
    rejected_stable = len(set(rejected_values)) <= 1
    doc = {
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_at": snapshots[0] if snapshot_stable else "",
        "snapshot_stable": snapshot_stable,
        "frozen": frozen_values[0] if frozen_stable and frozen_values else None,
        "rejected": rejected_values[0] if rejected_stable and rejected_values else None,
        "rejected_stable": rejected_stable and bool(rejected_values),
        "steps_omitted": omitted,
        "steps": steps,
        "edits": edits,
        "edits_hidden": hidden_edits,
        "downs": downs,
        "downs_hidden": hidden_downs,
        "dip_count": dip_count,
        "dip_max": dip_max,
        "gains": gains,
        "span": {
            "from_total": first_total,
            "to_total": last_total,
            "delta": (last_total - first_total) if first_total is not None and last_total is not None else None,
            "keys_added": keys_added,
            "keys_removed": keys_removed,
            "net_downs": net_downs,
        },
    }
    journal = None
    for candidate in reversed(folders):
        journal = _journal(candidate / "erreurs.log")
        if journal:
            break
    if journal:
        doc["journal"] = journal
    return doc


def _kept_journal(path: Path) -> dict | None:
    previous = _load_json(path)
    if not isinstance(previous, dict):
        return None
    old = previous.get("journal")
    if not isinstance(old, dict):
        return None
    lines = old.get("lines")
    http_404 = old.get("http_404")
    if (
        isinstance(lines, bool)
        or isinstance(http_404, bool)
        or not isinstance(lines, int)
        or not isinstance(http_404, int)
    ):
        return None
    if lines < 0 or http_404 < 0 or http_404 > lines or lines > 100_000:
        return None
    return {"lines": lines, "http_404": http_404, "other": lines - http_404}


def write_comparaison(
    pulls_path: Path | None = None,
    out_path: Path | None = None,
    *,
    site: bool = True,
) -> dict | None:
    """Écrit la comparaison. Sans aucune copie, ne touche pas le fichier."""
    if pulls_path is None:
        pulls_path = locate_pulls()
    history = ROOT / "data" / "historique"
    has_history = history.is_dir()
    has_external = pulls_path is not None and pulls_path.is_dir()
    if not has_history and not has_external:
        return None
    doc = build(pulls_path if has_external else history, ROOT / "data" if site else None)
    if doc is None:
        return None
    target = out_path or OUT
    if "journal" not in doc:
        kept = _kept_journal(target)
        if kept:
            doc["journal"] = kept
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if "\u2014" in payload or "\u2013" in payload:
        raise RuntimeError("tiret long dans la comparaison")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    return doc


def main() -> None:
    doc = write_comparaison()
    if doc is None:
        print("comparaison inchangée")
        return
    print(
        f"ok étapes={len(doc['steps'])} retouches={len(doc['edits'])} "
        f"cachées={doc['edits_hidden']} reculs={len(doc['downs'])} "
        f"hausses={len(doc['gains'])}"
    )


if __name__ == "__main__":
    main()
