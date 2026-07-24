"""Manga chapter-update trackers (MangaDex) with For You ranking."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from localfun.db.repositories import Repository
from localfun.paths import app_data_dir
from localfun.services.mangadex import MangaDexClient, MangaDexError

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def _cache_dir() -> Path:
    p = app_data_dir() / "cache" / "manga_latest"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _chapter_num(val: str | None) -> float | None:
    if val is None or val == "?" or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        m = re.search(r"[\d.]+", str(val))
        return float(m.group(0)) if m else None


def _cached_latest(
    client: MangaDexClient, mangadex_id: str, lang: str
) -> dict[str, Any] | None:
    path = _cache_dir() / f"{mangadex_id}_{lang}.json"
    if path.is_file() and time.time() - path.stat().st_mtime < 3600:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            pass
    try:
        latest = client.latest_chapter(mangadex_id, lang=lang)
    except MangaDexError:
        return None
    try:
        path.write_text(json.dumps(latest or {}), encoding="utf-8")
    except OSError:
        pass
    return latest


def build_manga_upcoming(
    repo: Repository,
    *,
    sort: str = "for_you",
    lang: str = "en",
    q: str = "",
) -> dict[str, Any]:
    client = MangaDexClient()
    library = [e for e in repo.list_user_manga() if e.mangadex_id]
    now = datetime.now(tz=timezone.utc)

    status_w = {
        "reading": 1.0,
        "plan_to_read": 0.9,
        "on_hold": 0.7,
        "completed": 0.25,
        "dropped": 0.1,
    }

    items: list[dict[str, Any]] = []
    # Prefer active lists first, cap network calls
    ranked_lib = sorted(
        library,
        key=lambda e: status_w.get(e.list_status, 0.3) * (1 + (e.score or 0) / 10),
        reverse=True,
    )

    for e in ranked_lib[:60]:
        assert e.mangadex_id
        latest = _cached_latest(client, e.mangadex_id, lang)
        if not latest:
            continue
        pub = _parse_iso(latest.get("publish_at"))
        age_secs = int((now - pub).total_seconds()) if pub else None
        # Treat "countdown" as time since last chapter (freshness); new unread = boost
        ch_num = _chapter_num(str(latest.get("chapter")))
        unread = (
            ch_num is not None
            and e.progress is not None
            and ch_num > float(e.progress)
        )
        looking = 55.0 * status_w.get(e.list_status, 0.4)
        if unread:
            looking += 35.0
        if age_secs is not None and age_secs < 7 * 86400:
            looking += 15.0 * (1 - age_secs / (7 * 86400))
        if e.score and e.score >= 7:
            looking += 5.0

        rem = {
            "label": _age_label(age_secs),
            "total_seconds": age_secs,
            "days": (age_secs // 86400) if age_secs is not None else None,
            "hours": None,
            "minutes": None,
        }
        reason = None
        if unread:
            reason = f"New chapter past your progress ({e.progress})"
        elif e.list_status == "reading":
            reason = "You're reading this"
        elif e.list_status == "plan_to_read":
            reason = "On your Plan to Read"
        elif e.list_status == "on_hold":
            reason = "On your On Hold list"

        items.append(
            {
                "user_manga_id": e.user_manga_id,
                "mangadex_id": e.mangadex_id,
                "title": e.title,
                "title_english": e.title_english,
                "display_title": e.title_english or e.title,
                "cover_url": e.cover_url,
                "list_status": e.list_status,
                "progress": e.progress,
                "latest_chapter": latest.get("chapter"),
                "latest_title": latest.get("title") or "",
                "latest_chapter_id": latest.get("id"),
                "publish_at": latest.get("publish_at"),
                "countdown_kind": "chapter",
                "countdown": rem,
                "looking_forward": round(min(100.0, looking), 1),
                "genre_match": 0.0,
                "unread": bool(unread),
                "reason": reason,
            }
        )
        time.sleep(0.12)

    qn = (q or "").strip().casefold()
    if qn:
        items = [
            i
            for i in items
            if qn in (i["display_title"] or "").casefold()
            or qn in (i["title"] or "").casefold()
            or qn in (i.get("latest_chapter") or "").casefold()
        ]

    if sort == "name":
        items.sort(key=lambda x: (x["display_title"] or "").casefold())
    elif sort == "time":
        # Most recently updated first
        items.sort(
            key=lambda x: (
                x["countdown"]["total_seconds"] is None,
                x["countdown"]["total_seconds"]
                if x["countdown"]["total_seconds"] is not None
                else 10**12,
            )
        )
    else:
        items.sort(
            key=lambda x: (
                -int(x.get("unread") or 0),
                -(x["looking_forward"]),
                x["countdown"]["total_seconds"] is None,
                x["countdown"]["total_seconds"]
                if x["countdown"]["total_seconds"] is not None
                else 10**12,
            )
        )

    return {
        "sort": sort,
        "lang": lang,
        "generated_at": now.isoformat(),
        "count": len(items),
        "items": items,
        "note": "Manga uses chapter update freshness (MangaDex), not weekly broadcast slots.",
    }


def _age_label(age_secs: int | None) -> str:
    if age_secs is None:
        return "Unknown"
    if age_secs < 3600:
        return f"{max(1, age_secs // 60)}m ago"
    if age_secs < 86400:
        return f"{age_secs // 3600}h ago"
    days = age_secs // 86400
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"
