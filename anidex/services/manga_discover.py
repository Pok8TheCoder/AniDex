"""Manga Discover rails via MangaDex + library genre/fuzzy matching."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from anidex.db.repositories import MangaEntry, Repository
from anidex.paths import app_data_dir
from anidex.services.anime_countdown import title_score
from anidex.services.mangadex import MangaDexClient, MangaDexError, MangaDexResult

_CACHE_TTL = 6 * 3600


def _cache_dir():
    p = app_data_dir() / "cache" / "manga_discover"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tags_from_raw(raw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    attrs = raw.get("attributes") or {}
    for tag in attrs.get("tags") or []:
        names = (tag.get("attributes") or {}).get("name") or {}
        name = names.get("en") or next(iter(names.values()), None)
        if name:
            out.append(str(name))
    return out


def _load_browse(
    client: MangaDexClient, order: str, *, limit: int = 40
) -> list[MangaDexResult]:
    disk = _cache_dir() / f"browse_{order}_{limit}.json"
    if disk.is_file() and time.time() - disk.stat().st_mtime < _CACHE_TTL:
        try:
            data = json.loads(disk.read_text(encoding="utf-8"))
            return [
                MangaDexResult(
                    mangadex_id=d["mangadex_id"],
                    title=d["title"],
                    title_english=d.get("title_english"),
                    status=d.get("status"),
                    year=d.get("year"),
                    cover_url=d.get("cover_url"),
                    synopsis=d.get("synopsis"),
                    raw=d.get("raw") or {},
                )
                for d in data
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    hits = client.browse_manga(order=order, limit=limit)
    try:
        payload = [
            {
                "mangadex_id": h.mangadex_id,
                "title": h.title,
                "title_english": h.title_english,
                "status": h.status,
                "year": h.year,
                "cover_url": h.cover_url,
                "synopsis": h.synopsis,
                "raw": h.raw,
            }
            for h in hits
        ]
        disk.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return hits


def _tag_weights(entries: list[MangaEntry], client: MangaDexClient) -> dict[str, float]:
    weights: dict[str, float] = {}
    status_w = {
        "reading": 3.0,
        "completed": 2.0,
        "plan_to_read": 1.5,
        "on_hold": 1.0,
        "dropped": 0.2,
    }
    cache = _cache_dir() / "tags"
    cache.mkdir(parents=True, exist_ok=True)
    ranked = sorted(
        [e for e in entries if e.mangadex_id],
        key=lambda e: status_w.get(e.list_status, 0.5) * (1 + (e.score or 0) / 10),
        reverse=True,
    )[:20]
    for e in ranked:
        assert e.mangadex_id
        path = cache / f"{e.mangadex_id}.json"
        tags: list[str] = []
        if path.is_file() and time.time() - path.stat().st_mtime < 30 * 86400:
            try:
                tags = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                tags = []
        if not tags:
            try:
                detail = client.get_manga(e.mangadex_id)
                tags = _tags_from_raw(detail.raw)
                path.write_text(json.dumps(tags), encoding="utf-8")
                time.sleep(0.15)
            except MangaDexError:
                continue
        w = status_w.get(e.list_status, 0.5)
        if e.score and e.score >= 7:
            w *= 1.3
        for t in tags:
            weights[t] = weights.get(t, 0.0) + w
    if weights:
        mx = max(weights.values()) or 1.0
        weights = {k: v / mx for k, v in weights.items()}
    return weights


def _looking_forward(hit: MangaDexResult, library: list[MangaEntry]) -> tuple[float, str | None]:
    titles = [hit.title, hit.title_english or ""]
    best = 0.0
    matched = None
    boost = {
        "plan_to_read": 1.0,
        "reading": 0.95,
        "on_hold": 0.75,
        "completed": 0.35,
        "dropped": 0.1,
    }
    for e in library:
        for ut in (e.title_english, e.title):
            if not ut:
                continue
            for t in titles:
                if not t:
                    continue
                s = title_score(ut, t)
                if s < 55:
                    continue
                b = s * boost.get(e.list_status, 0.4)
                if b > best:
                    best = b
                    matched = e.list_status
    return best, matched


def _card(
    hit: MangaDexResult,
    *,
    library_ids: set[str],
    library_status: dict[str, str],
    tag_w: dict[str, float],
    library: list[MangaEntry],
) -> dict[str, Any]:
    tags = _tags_from_raw(hit.raw)
    lf, lf_status = _looking_forward(hit, library)
    g = 0.0
    if tag_w and tags:
        g = (sum(tag_w.get(t, 0.0) for t in tags) / len(tags)) * 100.0
    mid = hit.mangadex_id
    in_lib = mid in library_ids
    for_you = lf * 0.55 + g * 0.45
    reason = None
    if lf >= 55:
        reason = "Matches your library"
    elif g >= 40:
        reason = "Fits your tags"
    return {
        "mangadex_id": mid,
        "title": hit.title,
        "title_english": hit.title_english,
        "display_title": hit.title_english or hit.title,
        "status": hit.status,
        "year": hit.year,
        "cover_url": f"/api/manga/cover/{mid}" if mid else hit.cover_url,
        "synopsis": (hit.synopsis or "")[:280],
        "genres": tags[:8],
        "looking_forward": round(lf, 1),
        "genre_match": round(g, 1),
        "for_you_score": round(for_you, 1),
        "in_library": in_lib,
        "list_status": library_status.get(mid),
        "matched_list_status": lf_status,
        "reason": reason,
    }


def build_manga_discover(
    repo: Repository,
    *,
    limit: int = 24,
) -> dict[str, Any]:
    client = MangaDexClient()
    library = repo.list_user_manga()
    library_ids = {e.mangadex_id for e in library if e.mangadex_id}
    library_status = {
        e.mangadex_id: e.list_status for e in library if e.mangadex_id
    }
    tag_w = _tag_weights(library, client)

    errors: list[str] = []
    popular: list[MangaDexResult] = []
    rated: list[MangaDexResult] = []
    recent: list[MangaDexResult] = []
    try:
        popular = _load_browse(client, "followedCount", limit=40)
    except MangaDexError as e:
        errors.append(str(e))
    try:
        rated = _load_browse(client, "rating", limit=40)
    except MangaDexError as e:
        errors.append(str(e))
    try:
        recent = _load_browse(client, "latestUploadedChapter", limit=40)
    except MangaDexError as e:
        errors.append(str(e))

    def cards(hits: list[MangaDexResult], *, exclude_library: bool = False):
        out = []
        for h in hits:
            if exclude_library and h.mangadex_id in library_ids:
                st = library_status.get(h.mangadex_id)
                if st and st != "plan_to_read":
                    continue
            out.append(
                _card(
                    h,
                    library_ids=library_ids,
                    library_status=library_status,
                    tag_w=tag_w,
                    library=library,
                )
            )
        return out

    pool: dict[str, MangaDexResult] = {}
    for h in popular + rated + recent:
        pool.setdefault(h.mangadex_id, h)

    for_you = cards(list(pool.values()), exclude_library=True)
    for_you.sort(key=lambda x: (-x["for_you_score"], -x["genre_match"]))
    for_you = for_you[:limit]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_genres": sorted(tag_w.items(), key=lambda kv: -kv[1])[:8],
        "for_you": for_you,
        "popular": cards(popular)[:limit],
        "top_rated": cards(rated)[:limit],
        "recent": cards(recent)[:limit],
        "errors": errors,
        "library_count": len(library),
    }
