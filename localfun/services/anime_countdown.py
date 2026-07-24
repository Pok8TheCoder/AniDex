"""Upcoming anime countdowns with personalized For You ranking."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from localfun.db.repositories import AnimeEntry, Repository
from localfun.paths import app_data_dir
from localfun.services.mal_api import MalClient, MalApiError

try:
    from zoneinfo import ZoneInfo

    JST = ZoneInfo("Asia/Tokyo")
except Exception:  # noqa: BLE001 — Windows without tzdata
    JST = timezone(timedelta(hours=9), name="JST")
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")
_SEASON_TAIL = re.compile(
    r"\s+(?:"
    r"Season\s+\d+"
    r"|S\d+\b"
    r"|\d+(?:st|nd|rd|th)\s+Season"
    r"|Part\s+\d+"
    r"|Cour\s+\d+"
    r")\s*$",
    re.I,
)

# In-memory season cache: key -> (expires_at, payload)
_SEASON_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL = 6 * 3600


def current_season(now: datetime | None = None) -> tuple[int, str]:
    now = now or datetime.now(tz=JST)
    m = now.month
    if m <= 3:
        return now.year, "winter"
    if m <= 6:
        return now.year, "spring"
    if m <= 9:
        return now.year, "summer"
    return now.year, "fall"


def next_season(year: int, season: str) -> tuple[int, str]:
    order = ["winter", "spring", "summer", "fall"]
    i = order.index(season)
    if i == 3:
        return year + 1, "winter"
    return year, order[i + 1]


def _norm(text: str) -> str:
    t = (text or "").casefold()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def _base_title(text: str) -> str:
    return _norm(_SEASON_TAIL.sub("", text or ""))


def title_score(query: str, candidate: str) -> float:
    """0–100 fuzzy title similarity (same spirit as Pahe matching)."""
    q = _norm(query)
    c = _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 100.0
    qb, cb = _base_title(query), _base_title(candidate)
    if qb and cb and qb == cb:
        return 92.0
    if q in c or c in q:
        return 70.0 + 20.0 * (min(len(q), len(c)) / max(len(q), len(c)))
    if qb and cb and (qb in cb or cb in qb):
        return 65.0
    qt, ct = set(q.split()), set(c.split())
    if not qt or not ct:
        return 0.0
    overlap = len(qt & ct) / max(len(qt), len(ct))
    return overlap * 55.0


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y":
                dt = dt.replace(month=1, day=1)
            elif fmt == "%Y-%m":
                dt = dt.replace(day=1)
            return dt.replace(tzinfo=JST)
        except ValueError:
            continue
    return None


_DOW = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def next_broadcast_at(
    broadcast: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Next air datetime from MAL broadcast (JST day + HH:MM)."""
    if not broadcast:
        return None
    day = (broadcast.get("day_of_the_week") or "").lower()
    start = broadcast.get("start_time") or ""
    if day not in _DOW or not start:
        return None
    try:
        hh, mm = [int(x) for x in start.split(":")[:2]]
    except ValueError:
        return None
    now = now or datetime.now(tz=JST)
    target_dow = _DOW[day]
    days_ahead = (target_dow - now.weekday()) % 7
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def countdown_target(node: dict[str, Any], *, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Return (when, kind) where kind is premiere|episode|tba."""
    now = now or datetime.now(tz=JST)
    status = (node.get("status") or "").lower()
    start = _parse_date(node.get("start_date"))
    broadcast = node.get("broadcast") or {}

    if status == "not_yet_aired":
        if start and start > now:
            return start, "premiere"
        nxt = next_broadcast_at(broadcast, now=now)
        if nxt:
            return nxt, "premiere"
        return start, "premiere" if start else "tba"

    if status == "currently_airing":
        nxt = next_broadcast_at(broadcast, now=now)
        if nxt:
            return nxt, "episode"
        if start and start > now:
            return start, "premiere"
        return None, "tba"

    # finished — still show if somehow in season list
    return None, "tba"


def format_remaining(target: datetime | None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(tz=JST)
    if not target:
        return {
            "label": "TBA",
            "total_seconds": None,
            "days": None,
            "hours": None,
            "minutes": None,
        }
    delta = target - now
    secs = int(delta.total_seconds())
    if secs < 0:
        return {
            "label": "Airing now",
            "total_seconds": 0,
            "days": 0,
            "hours": 0,
            "minutes": 0,
        }
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        label = f"{days}d {hours}h"
    elif hours > 0:
        label = f"{hours}h {minutes}m"
    else:
        label = f"{minutes}m"
    return {
        "label": label,
        "total_seconds": secs,
        "days": days,
        "hours": hours,
        "minutes": minutes,
    }


def _genre_names(node: dict[str, Any]) -> list[str]:
    out = []
    for g in node.get("genres") or []:
        name = (g.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def _cache_path() -> Path:
    p = app_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_season(
    client: MalClient, year: int, season: str
) -> list[dict[str, Any]]:
    key = f"{year}-{season}"
    hit = _SEASON_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    disk = _cache_path() / f"season_{key}.json"
    if disk.is_file() and time.time() - disk.stat().st_mtime < _CACHE_TTL:
        try:
            nodes = json.loads(disk.read_text(encoding="utf-8"))
            if isinstance(nodes, list):
                _SEASON_CACHE[key] = (time.time() + _CACHE_TTL, nodes)
                return nodes
        except (OSError, json.JSONDecodeError):
            pass

    nodes: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch, _ = client.get_seasonal_anime(year, season, limit=100, offset=offset)
        if not batch:
            break
        nodes.extend(batch)
        offset += len(batch)
        if len(batch) < 100 or offset >= 500:
            break
        time.sleep(0.25)

    _SEASON_CACHE[key] = (time.time() + _CACHE_TTL, nodes)
    try:
        disk.write_text(json.dumps(nodes), encoding="utf-8")
    except OSError:
        pass
    return nodes


def _user_genre_weights(
    client: MalClient, entries: list[AnimeEntry]
) -> dict[str, float]:
    """Genre affinity from user's watching / completed / plan list."""
    weights: dict[str, float] = {}
    status_w = {
        "watching": 3.0,
        "completed": 2.0,
        "plan_to_watch": 1.5,
        "on_hold": 1.0,
        "dropped": 0.2,
    }
    cache_dir = _cache_path() / "genres"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Prefer recent / higher engagement first; cap API calls
    ranked = sorted(
        [e for e in entries if e.mal_id],
        key=lambda e: status_w.get(e.list_status, 0.5) * (1 + (e.score or 0) / 10),
        reverse=True,
    )[:24]

    for e in ranked:
        assert e.mal_id
        path = cache_dir / f"{e.mal_id}.json"
        genres: list[str] = []
        if path.is_file() and time.time() - path.stat().st_mtime < 30 * 86400:
            try:
                genres = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                genres = []
        if not genres:
            try:
                detail = client.get_anime(e.mal_id, fields="id,genres")
                genres = _genre_names(detail)
                path.write_text(json.dumps(genres), encoding="utf-8")
                time.sleep(0.2)
            except MalApiError:
                continue
        w = status_w.get(e.list_status, 0.5)
        if e.score and e.score >= 7:
            w *= 1.3
        for g in genres:
            weights[g] = weights.get(g, 0.0) + w

    # normalize
    if weights:
        mx = max(weights.values()) or 1.0
        weights = {k: v / mx for k, v in weights.items()}
    return weights


def _looking_forward(
    node: dict[str, Any], library: list[AnimeEntry]
) -> tuple[float, str | None, str | None]:
    """Return (score 0-100, matched_list_status, matched_title)."""
    alts = node.get("alternative_titles") or {}
    titles = [
        node.get("title") or "",
        alts.get("en") or "",
        *list((alts.get("synonyms") or [])[:5]),
    ]
    titles = [t for t in titles if t]
    best = 0.0
    matched_status = None
    matched_title = None
    status_boost = {
        "plan_to_watch": 1.0,
        "watching": 0.95,
        "on_hold": 0.75,
        "completed": 0.35,  # sequels / interest
        "dropped": 0.1,
    }
    for e in library:
        for ut in (e.title_english, e.title):
            if not ut:
                continue
            for t in titles:
                s = title_score(ut, t)
                if s < 55:
                    continue
                boosted = s * status_boost.get(e.list_status, 0.4)
                if boosted > best:
                    best = boosted
                    matched_status = e.list_status
                    matched_title = e.title_english or e.title
    return best, matched_status, matched_title


def _genre_affinity(node: dict[str, Any], weights: dict[str, float]) -> float:
    if not weights:
        return 0.0
    genres = _genre_names(node)
    if not genres:
        return 0.0
    score = sum(weights.get(g, 0.0) for g in genres) / len(genres)
    return score * 100.0


def build_upcoming(
    client: MalClient,
    repo: Repository,
    *,
    sort: str = "for_you",
    include_next_season: bool = True,
    media_filter: str = "all",
) -> dict[str, Any]:
    year, season = current_season()
    seasons = [(year, season)]
    if include_next_season:
        seasons.append(next_season(year, season))

    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for y, s in seasons:
        for n in _load_season(client, y, s):
            mid = n.get("id")
            if not mid or mid in seen:
                continue
            seen.add(int(mid))
            nodes.append(n)

    library = repo.list_user_anime()
    genre_w = _user_genre_weights(client, library)
    now = datetime.now(tz=JST)

    items: list[dict[str, Any]] = []
    for n in nodes:
        mt = (n.get("media_type") or "").lower()
        if media_filter != "all" and mt != media_filter:
            continue
        # Skip music / unknown noise
        if mt in {"music"}:
            continue
        status = (n.get("status") or "").lower()
        if status == "finished_airing":
            continue

        when, kind = countdown_target(n, now=now)
        rem = format_remaining(when, now=now)
        lf_score, lf_status, lf_title = _looking_forward(n, library)
        g_score = _genre_affinity(n, genre_w)
        alts = n.get("alternative_titles") or {}
        pic = n.get("main_picture") or {}
        ss = n.get("start_season") or {}

        items.append(
            {
                "mal_id": int(n["id"]),
                "title": n.get("title") or "",
                "title_english": alts.get("en"),
                "display_title": alts.get("en") or n.get("title") or "",
                "media_type": n.get("media_type"),
                "airing_status": n.get("status"),
                "episodes": n.get("num_episodes"),
                "mean_score": n.get("mean"),
                "cover_url": pic.get("large") or pic.get("medium"),
                "synopsis": (n.get("synopsis") or "")[:280],
                "genres": _genre_names(n),
                "start_date": n.get("start_date"),
                "broadcast": n.get("broadcast"),
                "season": {
                    "year": ss.get("year") or year,
                    "season": ss.get("season") or season,
                },
                "countdown_kind": kind,
                "airs_at": when.isoformat() if when else None,
                "countdown": rem,
                "looking_forward": round(lf_score, 1),
                "genre_match": round(g_score, 1),
                "matched_list_status": lf_status,
                "matched_library_title": lf_title,
                "reason": _reason(lf_score, lf_status, g_score),
            }
        )

    if sort == "name":
        items.sort(key=lambda x: (x["display_title"] or "").casefold())
    elif sort == "time":
        items.sort(
            key=lambda x: (
                x["countdown"]["total_seconds"] is None,
                x["countdown"]["total_seconds"]
                if x["countdown"]["total_seconds"] is not None
                else 10**12,
            )
        )
    else:  # for_you
        items.sort(
            key=lambda x: (
                -(x["looking_forward"]),
                -(x["genre_match"]),
                x["countdown"]["total_seconds"] is None,
                x["countdown"]["total_seconds"]
                if x["countdown"]["total_seconds"] is not None
                else 10**12,
            )
        )

    return {
        "season": {"year": year, "season": season},
        "next_season": {"year": seasons[-1][0], "season": seasons[-1][1]}
        if include_next_season
        else None,
        "sort": sort,
        "generated_at": now.isoformat(),
        "count": len(items),
        "items": items,
        "top_genres": sorted(genre_w.items(), key=lambda kv: -kv[1])[:8],
    }


def _reason(lf: float, status: str | None, genre: float) -> str | None:
    if lf >= 70 and status == "plan_to_watch":
        return "On your Plan to Watch"
    if lf >= 70 and status == "watching":
        return "You're watching this"
    if lf >= 60 and status == "on_hold":
        return "On your On Hold list"
    if lf >= 55 and status == "completed":
        return "Related to something you finished"
    if lf >= 55:
        return "Matches your library"
    if genre >= 45:
        return "Fits your genres"
    return None
