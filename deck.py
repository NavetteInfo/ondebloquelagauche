#!/usr/bin/env python3
"""Empile les tranches deck déjà déposées sur la base publiée.

Chaque JSON ne couvre qu'une partie de la liste. Une tranche met à jour
ou ajoute ses fiches. Elle n'efface pas les fiches absentes. Le chiffre d'une fiche vient d'un JSON déjà déposé dans fcounts, sinon
de la carte deck. Les fiches absentes de ces fichiers gardent le dernier
chiffre déjà publié. Un fichier qui demande une session est ignoré.
Ce passage ne lit pas /api/counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import refresh

ROOT = refresh.ROOT
DATA = refresh.DATA
MERGED = DATA / "merged.json"
DECK_DIR = DATA / "deck"
FCOUNTS_DIR = DATA / "fcounts"
MIN_FICHES = 1000
FULL_FCOUNTS = 1000
HISTORICAL_BASE = 1_400_000


def _pulls_root() -> Path | None:
    env = os.environ.get("PULLS_DIR", "").strip()
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    for candidate in (Path("/pulls"), ROOT.parent / "pulls"):
        if candidate.is_dir():
            return candidate
    return None


def _deck_sources() -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    candidates = []
    pulls = _pulls_root()
    if pulls is not None:
        candidates.append(pulls / "deck")
    candidates.append(DECK_DIR)
    for folder in candidates:
        if not folder.is_dir() or folder in seen:
            continue
        seen.add(folder)
        files = [
            path for path in folder.glob("deck_*.json")
            if path.is_file() and not path.is_symlink()
        ]
        found.extend(sorted(files, key=lambda path: path.name))
    return found


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_accounts() -> tuple[dict, str]:
    """Le dernier fichier accounts, pas le plus long : les retraits déjà faits restent."""
    folder = refresh.HISTORIQUE / "accounts"
    if not folder.is_dir():
        raise RuntimeError("base accounts introuvable")
    paths = [
        path for path in folder.glob("accounts_*.json")
        if path.is_file() and not path.is_symlink()
    ]
    for path in sorted(paths, key=lambda item: item.name, reverse=True):
        doc = _read_json(path)
        entries = doc.get("entries") if doc else None
        if isinstance(entries, list) and len(entries) >= MIN_FICHES:
            return doc, path.name
    raise RuntimeError("base accounts introuvable")


def _latest_script(folder: Path | None, prefix: str) -> Path | None:
    if folder is None or not folder.is_dir():
        return None
    files = [
        path for path in folder.glob(f"{prefix}_*.js")
        if path.is_file() and not path.is_symlink()
    ]
    if not files:
        return None
    return sorted(files, key=lambda path: path.name)[-1]


def _load_directory(state: dict) -> tuple[list, dict]:
    curated = state.get("curated")
    socials = state.get("socials")
    if isinstance(curated, list) and curated and isinstance(socials, dict):
        return curated, socials
    pulls = _pulls_root()
    accounts_path = _latest_script(None if pulls is None else pulls / "accounts_js", "accounts_js")
    socials_path = _latest_script(None if pulls is None else pulls / "socials_js", "socials_js")
    if accounts_path is None:
        stored = refresh.RAW / "accounts.js"
        accounts_path = stored if stored.is_file() else None
    if socials_path is None:
        stored = refresh.RAW / "socials.js"
        socials_path = stored if stored.is_file() else None
    if accounts_path is None or socials_path is None:
        return [], {}
    people = refresh._parse_assigned(accounts_path.read_text(encoding="utf-8"), "accounts")
    links = refresh._parse_assigned(socials_path.read_text(encoding="utf-8"), "socials")
    if not isinstance(people, list) or not isinstance(links, dict):
        return [], {}
    return people, links


def _baseline() -> dict:
    if MERGED.is_file():
        doc = _read_json(MERGED)
        entries = doc.get("entries") if doc else None
        if isinstance(entries, list) and len(entries) >= MIN_FICHES:
            return doc
    accounts, name = _latest_accounts()
    return {
        "entries": accounts.get("entries"),
        "rejected_removals": accounts.get("rejected_removals") or [],
        "frozen": accounts.get("frozen"),
        "snapshot_at": accounts.get("snapshot_at"),
        "baseline_accounts": name,
        "curated": None,
        "socials": None,
    }


def _text(value, limit: int) -> str:
    raw = str(value or "").replace("\u2019", "'").replace("\u2018", "'")
    return refresh._clip(raw, limit)


def _aliases(card: dict) -> list[str]:
    found: list[str] = []
    raw = [card.get("id")]
    extra = card.get("aliases")
    if isinstance(extra, list):
        raw.extend(extra)
    for value in raw:
        if not isinstance(value, str):
            continue
        text = value.strip().lower()
        if not text or len(text) > 200 or any(ord(char) < 32 for char in text):
            continue
        if text not in found:
            found.append(text)
    return found


def _links(card: dict) -> dict[str, str]:
    raw = card.get("links")
    if not isinstance(raw, dict):
        return {}
    links: dict[str, str] = {}
    for net, url in raw.items():
        if net not in refresh.PLATFORMS or not isinstance(url, str):
            continue
        safe = refresh.safe_profile_url(url)
        if safe:
            links[net] = safe
    return links


def _category(value) -> str:
    text = _text(value, 80)
    if text in refresh._DIR_CATS:
        return text
    return "Autres"


def _apply(state: dict, cards: list[dict]) -> dict:
    entries = [entry for entry in state.get("entries") or [] if isinstance(entry, dict)]
    curated, socials = _load_directory(state)
    people = [person for person in curated if isinstance(person, dict)]
    links = dict(socials)
    seen_aliases: set[str] = set()
    parsed: list[tuple[dict, list[str], dict[str, str]]] = []
    for card in cards:
        aliases = _aliases(card)
        if not aliases:
            continue
        seen_aliases.update(aliases)
        parsed.append((card, aliases, _links(card)))
    if seen_aliases:
        kept = []
        for entry in entries:
            if entry.get("type") == "removal":
                target = str(entry.get("target_id") or "").strip().lower()
                if target and target in seen_aliases:
                    continue
            kept.append(entry)
        entries = kept
    by_person: dict[str, dict] = {}
    for person in people:
        key = refresh._person_key(person)
        if key and key not in by_person:
            by_person[key] = person
        handle = str(person.get("handle") or "").strip().lower()
        if handle and handle not in by_person:
            by_person[handle] = person
    by_id: dict[tuple[str, str], dict] = {}
    by_url: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") == "removal":
            continue
        platform = str(entry.get("platform") or "").strip().lower()
        ident = str(entry.get("id") or "").strip().lower()
        url = refresh.safe_profile_url(str(entry.get("profile_url") or ""))
        if platform and ident:
            by_id.setdefault((platform, ident), entry)
        if url:
            by_url.setdefault(url, entry)
    added = 0
    updated = 0
    fallback: dict[str, int] = {}
    for card, aliases, slot in parsed:
        name = _text(card.get("name"), 180)
        category = _category(card.get("category"))
        reason = _text(card.get("reason"), 400)
        person = None
        for alias in aliases:
            person = by_person.get(alias)
            if person is not None:
                break
        if person is not None:
            if name:
                person["name"] = name
            if category:
                person["category"] = category
            if reason:
                person["reason"] = reason
            key = refresh._person_key(person)
            if key and slot:
                current = links.get(key)
                if not isinstance(current, dict):
                    current = {}
                    links[key] = current
                for platform, url in slot.items():
                    current[platform] = url
        counts = card.get("c")
        if isinstance(counts, dict):
            for alias, value in counts.items():
                if not isinstance(alias, str):
                    continue
                number = refresh._count(value)
                if not number:
                    continue
                for platform in slot or {"x"}:
                    fallback[f"{platform}:{alias.strip().lower()}"] = number
        for platform, url in slot.items():
            hit = None
            for alias in aliases:
                hit = by_id.get((platform, alias))
                if hit is not None:
                    break
            if hit is None:
                hit = by_url.get(url)
            if hit is None and person is not None:
                key = refresh._person_key(person)
                current = links.get(key) if isinstance(links.get(key), dict) else {}
                handle = str(person.get("handle") or "")
                if platform in current or (
                    platform == "x" and refresh._HANDLE_RE.fullmatch(handle)
                ):
                    continue
            if hit is None:
                if not name:
                    continue
                hit = {
                    "id": aliases[0],
                    "type": "new",
                    "target_id": None,
                    "name": name,
                    "category": category or "Autres",
                    "platform": platform,
                    "profile_url": url,
                    "reason": reason,
                    "verified": 1 if card.get("verified") else 0,
                }
                entries.append(hit)
                by_id[(platform, aliases[0])] = hit
                by_url[url] = hit
                added += 1
                continue
            changed = False
            if name and hit.get("name") != name:
                hit["name"] = name
                changed = True
            if category and hit.get("category") != category:
                hit["category"] = category
                changed = True
            if reason and hit.get("reason") != reason:
                hit["reason"] = reason
                changed = True
            if not refresh.safe_profile_url(str(hit.get("profile_url") or "")):
                hit["profile_url"] = url
                changed = True
            if changed:
                updated += 1
    state["entries"] = entries
    state["curated"] = people
    state["socials"] = links
    state["cards"] = len({alias for _card, aliases, _slot in parsed for alias in aliases[:1]})
    state["added"] = added
    state["updated"] = updated
    state["fallback_counts"] = fallback
    return state


def _public_deck(doc: dict | None) -> dict | None:
    """Garde les cartes utiles. La notice et le champ t ne sont pas recopiés."""
    if not isinstance(doc, dict):
        return None
    rows = doc.get("cards")
    if not isinstance(rows, list):
        return None
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cards.append({key: value for key, value in row.items() if key not in {"t", "notice"}})
    if not cards:
        return None
    public: dict = {"cards": cards}
    if "quota" in doc:
        public["quota"] = bool(doc.get("quota"))
    total = doc.get("total")
    if isinstance(total, dict):
        public["total"] = total
    return public


def _deck_bytes(doc: dict) -> bytes:
    payload = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (payload + "\n").encode("utf-8")


def _copy_decks(paths: list[Path]) -> list[str]:
    DECK_DIR.mkdir(parents=True, exist_ok=True)
    for path in list(DECK_DIR.glob("deck_*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        public = _public_deck(_read_json(path))
        if public is None:
            path.unlink()
            continue
        payload = _deck_bytes(public)
        if path.read_bytes() != payload:
            path.write_bytes(payload)
    known: dict[str, str] = {}
    for path in DECK_DIR.glob("deck_*.json"):
        if path.is_file() and not path.is_symlink():
            known[hashlib.sha256(path.read_bytes()).hexdigest()] = path.name
    copied: list[str] = []
    for src in paths:
        public = _public_deck(_read_json(src))
        if public is None:
            continue
        payload = _deck_bytes(public)
        digest = hashlib.sha256(payload).hexdigest()
        if digest in known:
            continue
        target = DECK_DIR / src.name
        suffix = 2
        while target.exists():
            target = DECK_DIR / f"{src.stem}_{suffix}{src.suffix}"
            suffix += 1
        target.write_bytes(payload)
        known[digest] = target.name
        copied.append(target.name)
    return copied


def _fcount_sources() -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    candidates = []
    pulls = _pulls_root()
    if pulls is not None:
        candidates.append(pulls / "fcounts")
    candidates.append(FCOUNTS_DIR)
    for folder in candidates:
        if not folder.is_dir() or folder in seen:
            continue
        seen.add(folder)
        files = [
            path for path in folder.glob("fcounts_*.json")
            if path.is_file() and not path.is_symlink()
        ]
        found.extend(sorted(files, key=lambda path: path.name))
    return found


def _count_key(key: str) -> str | None:
    text = key.strip().lower()
    if not text or ":" not in text or len(text) > 220:
        return None
    platform, _, ident = text.partition(":")
    if platform not in refresh.PLATFORMS or not ident:
        return None
    if any(ord(char) < 32 for char in text):
        return None
    return text


def _take_counts(raw: dict, into: dict[str, int]) -> None:
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        name = _count_key(key)
        number = refresh._count(value)
        if name and number:
            into[name] = number


def _card_count_keys(card: dict) -> dict[str, int]:
    raw = card.get("c")
    if not isinstance(raw, dict):
        return {}
    found: dict[str, int] = {}
    platforms = list(_links(card)) or ["x"]
    for alias, value in raw.items():
        if not isinstance(alias, str):
            continue
        number = refresh._count(value)
        if not number:
            continue
        for platform in platforms:
            if platform in refresh.PLATFORMS:
                found[f"{platform}:{alias.strip().lower()}"] = number
    return found


def _public_fcounts(doc: dict | None) -> dict | None:
    """Garde les chiffres. Une demande de session, une notice ou un cookie ne passent pas."""
    if not isinstance(doc, dict) or doc.get("error") or doc.get("need_session"):
        return None
    counts: dict[str, int] = {}
    raw = doc.get("counts")
    if isinstance(raw, dict):
        _take_counts(raw, counts)
    cards = doc.get("cards")
    if isinstance(cards, list):
        for card in cards:
            if isinstance(card, dict):
                counts.update(_card_count_keys(card))
    if not counts and "counts" not in doc and "cards" not in doc:
        _take_counts(doc, counts)
    if not counts:
        return None
    public: dict = {"counts": counts}
    total = doc.get("total")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        public["total"] = total
    as_of = doc.get("as_of")
    if isinstance(as_of, str) and "T" in as_of and len(as_of) <= 40:
        public["as_of"] = as_of
    if isinstance(doc.get("live"), bool):
        public["live"] = doc["live"]
    historical = doc.get("historical_base")
    if isinstance(historical, int) and not isinstance(historical, bool) and historical > 0:
        public["historical_base"] = historical
    return public


def _read_fcounts() -> tuple[dict[str, int], dict]:
    counts: dict[str, int] = {}
    meta: dict = {"files": []}
    for path in _fcount_sources():
        public = _public_fcounts(_read_json(path))
        if public is None:
            continue
        counts.update(public["counts"])
        meta["files"].append(path.name)
        for field in ("total", "as_of", "live", "historical_base"):
            if field in public:
                meta[field] = public[field]
    meta["full"] = len(counts) >= FULL_FCOUNTS
    return counts, meta


def _copy_fcounts(paths: list[Path]) -> list[str]:
    FCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    for path in list(FCOUNTS_DIR.glob("fcounts_*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        public = _public_fcounts(_read_json(path))
        if public is None:
            path.unlink()
            continue
        payload = _deck_bytes(public)
        if path.read_bytes() != payload:
            path.write_bytes(payload)
    known: dict[str, str] = {}
    for path in FCOUNTS_DIR.glob("fcounts_*.json"):
        if path.is_file() and not path.is_symlink():
            known[hashlib.sha256(path.read_bytes()).hexdigest()] = path.name
    copied: list[str] = []
    for src in paths:
        public = _public_fcounts(_read_json(src))
        if public is None:
            continue
        payload = _deck_bytes(public)
        digest = hashlib.sha256(payload).hexdigest()
        if digest in known:
            continue
        target = FCOUNTS_DIR / src.name
        suffix = 2
        while target.exists():
            target = FCOUNTS_DIR / f"{src.stem}_{suffix}{src.suffix}"
            suffix += 1
        target.write_bytes(payload)
        known[digest] = target.name
        copied.append(target.name)
    return copied


def _held_by_url(previous: dict | None) -> dict[tuple[str, str], int]:
    """Dernier chiffre déjà publié, par réseau et URL. Pas une nouvelle lecture."""
    found: dict[tuple[str, str], int] = {}
    if not isinstance(previous, dict):
        return found
    for fiche in previous.get("fiches") or []:
        if not isinstance(fiche, dict):
            continue
        platform = str(fiche.get("platform") or "").strip().lower()
        url = refresh.safe_profile_url(str(fiche.get("url") or ""))
        number = fiche.get("count")
        if platform not in refresh.PLATFORMS or not url:
            continue
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        if number < 0 or number > refresh._COUNT_CAP:
            continue
        found[(platform, url)] = number
    return found


def publish() -> dict:
    sources = _deck_sources()
    cards: list[dict] = []
    used: list[str] = []
    for path in sources:
        doc = _read_json(path)
        rows = doc.get("cards") if doc else None
        if not isinstance(rows, list):
            continue
        chunk = [row for row in rows if isinstance(row, dict)]
        if not chunk:
            continue
        cards.extend(chunk)
        used.append(path.name)
    state = _apply(_baseline(), cards)
    if len(state.get("entries") or []) < MIN_FICHES:
        raise RuntimeError("merge trop court")
    if len(state.get("curated") or []) < 100:
        raise RuntimeError("liste affichée absente")
    previous = _read_json(refresh.FICHES) or {}
    fcounts, fmeta = _read_fcounts()
    counts = dict(state.get("fallback_counts") or {})
    counts.update(fcounts)
    refreshed = datetime.now(timezone.utc).replace(microsecond=0)
    refreshed_at = refreshed.strftime("%Y-%m-%dT%H:%M:%SZ")
    directory = (state.get("curated") or [], state.get("socials") or {})
    built = refresh.merge(
        {
            "entries": state["entries"],
            "snapshot_at": state.get("snapshot_at"),
        },
        {
            "counts": counts,
            "total": 0,
            "as_of": refreshed_at,
            "live": False,
        },
        refreshed_at,
        directory if directory[0] else None,
    )
    held = _held_by_url(previous)
    for fiche in built["fiches"]:
        if fiche["count"]:
            continue
        number = held.get((fiche["platform"], fiche["url"]))
        if number:
            fiche["count"] = number
    built["fiches"].sort(key=lambda fiche: (-fiche["count"], fiche["name"].casefold()))
    built["fiche_count_sum"] = sum(fiche["count"] for fiche in built["fiches"])
    historical = previous.get("historical_base")
    if not isinstance(historical, int) or historical <= 0:
        historical = HISTORICAL_BASE
    built["historical_base"] = historical
    if fmeta.get("full") and refresh._count(fmeta.get("total")):
        built["total_blocks"] = refresh._count(fmeta.get("total"))
        if isinstance(fmeta.get("as_of"), str):
            built["as_of"] = fmeta["as_of"]
        built["live"] = bool(fmeta.get("live"))
        file_historical = fmeta.get("historical_base")
        if isinstance(file_historical, int) and file_historical > 0:
            built["historical_base"] = file_historical
        built["source"] = "merged.json + fcounts"
    else:
        built["total_blocks"] = built["fiche_count_sum"]
        built["live"] = False
        built["as_of"] = refreshed_at
        built["source"] = "merged.json + deck" + (" + fcounts" if fcounts else "")
    refresh.attach_followers(built)
    old_count = previous.get("fiche_count")
    old_sum = previous.get("fiche_count_sum")
    if built["fiche_count"] < MIN_FICHES:
        raise RuntimeError(f"fiches trop courtes : {built['fiche_count']}")
    if isinstance(old_count, int) and built["fiche_count"] + 50 < old_count:
        raise RuntimeError(
            f"le merge passe de {old_count} à {built['fiche_count']} fiches"
        )
    if isinstance(old_sum, int) and old_sum > 0 and built["fiche_count_sum"] * 2 < old_sum:
        raise RuntimeError(
            f"la somme passe de {old_sum} à {built['fiche_count_sum']}"
        )
    copied = _copy_decks(sources)
    copied_counts = _copy_fcounts(_fcount_sources())
    state["merged_at"] = refreshed_at
    state["deck_files"] = used
    state["source"] = built["source"]
    public = {
        "merged_at": refreshed_at,
        "source": built["source"],
        "baseline_accounts": state.get("baseline_accounts"),
        "snapshot_at": state.get("snapshot_at"),
        "frozen": state.get("frozen"),
        "deck_files": used,
        "cards": state.get("cards") or 0,
        "added": state.get("added") or 0,
        "updated": state.get("updated") or 0,
        "entries": state["entries"],
        "curated": state.get("curated") or [],
        "socials": state.get("socials") or {},
        "rejected_removals": state.get("rejected_removals") or [],
    }
    refresh._write_json(MERGED, public)
    refresh._write_json(refresh.FICHES, built)
    return {
        "fiches": built["fiche_count"],
        "names": built["name_count"],
        "total": built["total_blocks"],
        "as_of": built.get("as_of"),
        "source": built["source"],
        "cards": public["cards"],
        "added": public["added"],
        "updated": public["updated"],
        "copied": copied,
        "fcounts": len(fcounts),
        "copied_counts": copied_counts,
        "refreshed_at": refreshed_at,
    }


def main() -> None:
    info = publish()
    print(
        f"ok fiches={info['fiches']} total={info['total']} "
        f"as_of={info['as_of']} source={info['source']} "
        f"cartes={info['cards']} ajouts={info['added']} "
        f"maj={info['updated']} copies={len(info['copied'])} "
        f"fcounts={info['fcounts']}"
    )


if __name__ == "__main__":
    main()
