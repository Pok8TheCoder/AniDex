"""Discover / front-page anime: popular, highly rated, and For You picks."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from anidex.db.repositories import Repository
from anidex.services.anime_countdown import (
    _CACHE_TTL,
    _cache_path,
    _genre_affinity,
    _genre_names,
    _looking_forward,
    _load_season,
    _reason,
    _user_genre_weights,
    current_season,
)
from anidex.services.mal_api import MalApiError, MalClient

_RANK_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _load_ranking(
    client: MalClient, ranking_type: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    key = f"rank:{ranking_type}:{limit}"
    hit = _RANK_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    disk = _cache_path() / f"rank_{ranking_type}_{limit}.json"
    if disk.is_file() and time.time() - disk.stat().st_mtime < _CACHE_TTL:
        try:
            nodes = json.loads(disk.read_text(encoding="utf-8"))
            if isinstance(nodes, list):
                _RANK_CACHE[key] = (time.time() + _CACHE_TTL, nodes)
                return nodes
        except (OSError, json.JSONDecodeError):
            pass

    nodes = client.get_anime_ranking(ranking_type, limit=limit, offset=0)
    _RANK_CACHE[key] = (time.time() + _CACHE_TTL, nodes)
    try:
        disk.write_text(json.dumps(nodes), encoding="utf-8")
    except OSError:
        pass
    return nodes


def _node_card(
    n: dict[str, Any],
    *,
    library_ids: set[int],
    library_status: dict[int, str],
    genre_w: dict[str, float],
    library,
) -> dict[str, Any]:
    mid = int(n["id"])
    alts = n.get("alternative_titles") or {}
    pic = n.get("main_picture") or {}
    lf_score, lf_status, lf_title = _looking_forward(n, library)
    g_score = _genre_affinity(n, genre_w)
    mean = n.get("mean")
    popularity = n.get("popularity")
    users = n.get("num_list_users") or 0
    # Blend: genre + fuzzy library match + MAL quality signals
    quality = 0.0
    if isinstance(mean, (int, float)) and mean:
        quality += float(mean) * 8.0  # ~0–80
    if users:
        quality += min(15.0, (users / 500_000) * 15.0)
    for_you = lf_score * 0.55 + g_score * 0.35 + quality * 0.15

    in_library = mid in library_ids
    return {
        "mal_id": mid,
        "title": n.get("title") or "",
        "title_english": alts.get("en"),
        "display_title": alts.get("en") or n.get("title") or "",
        "media_type": n.get("media_type"),
        "airing_status": n.get("status"),
        "episodes": n.get("num_episodes"),
        "mean_score": mean,
        "popularity": popularity,
        "rank": n.get("_rank") or n.get("rank"),
        "num_list_users": users,
        "cover_url": pic.get("large") or pic.get("medium"),
        "synopsis": (n.get("synopsis") or "")[:280],
        "genres": _genre_names(n),
        "looking_forward": round(lf_score, 1),
        "genre_match": round(g_score, 1),
        "for_you_score": round(for_you, 1),
        "matched_list_status": lf_status,
        "matched_library_title": lf_title,
        "in_library": in_library,
        "list_status": library_status.get(mid),
        "reason": _reason(lf_score, lf_status, g_score)
        or (
            f"MAL {mean:.2f}"
            if isinstance(mean, (int, float)) and mean
            else None
        ),
    }


def build_discover(
    client: MalClient,
    repo: Repository,
    *,
    limit: int = 24,
) -> dict[str, Any]:
    """Popular + highly rated rails, plus personalized For You from those pools."""
    library = repo.list_user_anime()
    library_ids = {int(e.mal_id) for e in library if e.mal_id}
    library_status = {int(e.mal_id): e.list_status for e in library if e.mal_id}
    genre_w = _user_genre_weights(client, library)

    popular_nodes: list[dict[str, Any]] = []
    rated_nodes: list[dict[str, Any]] = []
    season_nodes: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        popular_nodes = _load_ranking(client, "bypopularity", limit=50)
    except MalApiError as e:
        errors.append(str(e))
    try:
        rated_nodes = _load_ranking(client, "all", limit=50)
    except MalApiError as e:
        errors.append(str(e))
    try:
        y, s = current_season()
        season_nodes = _load_season(client, y, s)[:40]
    except MalApiError as e:
        errors.append(str(e))

    # Merge unique pool for For You
    pool: dict[int, dict[str, Any]] = {}
    for n in popular_nodes + rated_nodes + season_nodes:
        mid = n.get("id")
        if not mid:
            continue
        mid = int(mid)
        if mid not in pool:
            pool[mid] = n

    def cards(nodes: list[dict[str, Any]], *, exclude_library: bool = False) -> list[dict]:
        out = []
        for n in nodes:
            mid = n.get("id")
            if not mid:
                continue
            mid = int(mid)
            if exclude_library and mid in library_ids:
                # Still allow plan_to_watch to show as “on your list”
                st = library_status.get(mid)
                if st and st != "plan_to_watch":
                    continue
            mt = (n.get("media_type") or "").lower()
            if mt in {"music"}:
                continue
            out.append(
                _node_card(
                    n,
                    library_ids=library_ids,
                    library_status=library_status,
                    genre_w=genre_w,
                    library=library,
                )
            )
        return out

    popular = cards(popular_nodes)[:limit]
    top_rated = sorted(
        cards(rated_nodes),
        key=lambda x: (
            -(x["mean_score"] or 0),
            -(x["num_list_users"] or 0),
        ),
    )[:limit]

    for_you = cards(list(pool.values()), exclude_library=True)
    for_you.sort(
        key=lambda x: (
            -(x["for_you_score"]),
            -(x["genre_match"]),
            -(x["mean_score"] or 0),
        )
    )
    for_you = for_you[:limit]

    # Season spotlight (this season, sorted by popularity / for you)
    season = cards(season_nodes)[:limit]
    season.sort(
        key=lambda x: (-(x["for_you_score"]), -(x["num_list_users"] or 0))
    )

    y, s = current_season()
    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(),
        "season": {"year": y, "season": s},
        "top_genres": sorted(genre_w.items(), key=lambda kv: -kv[1])[:8],
        "for_you": for_you,
        "popular": popular,
        "top_rated": top_rated,
        "season_picks": season,
        "errors": errors,
        "library_count": len(library),
    }
