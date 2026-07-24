"""Helpers to serialize / apply AnimePahe catalog rows for peer sync."""

from __future__ import annotations

from typing import Any

from anidex.db.repositories import Repository


def export_pahe_for_entry(repo: Repository, *, mal_id: int, user_anime_id: int) -> dict[str, Any] | None:
    link = repo.get_pahe_link(user_anime_id)
    if not link:
        return None
    seasons_out: list[dict[str, Any]] = []
    for s in repo.list_pahe_seasons(link.id):
        eps = [
            {
                "episode": ep.episode,
                "episode2": ep.episode2,
                "episode_session": ep.episode_session,
                "title": ep.title,
                "snapshot": ep.snapshot,
                "duration": ep.duration,
                "disc": ep.disc,
            }
            for ep in repo.list_pahe_episodes(s.id)
        ]
        seasons_out.append(
            {
                "pahe_session": s.pahe_session,
                "label": s.label,
                "year": s.year,
                "episode_count": s.episode_count or len(eps),
                "sort_order": s.sort_order,
                "episodes": eps,
            }
        )
    return {
        "key": f"pahe:{mal_id}",
        "mal_id": mal_id,
        "pahe_session": link.pahe_session,
        "pahe_title": link.pahe_title,
        "poster": link.poster,
        "synced_at": link.synced_at or "",
        "updated_at": link.synced_at or "",
        "seasons": seasons_out,
    }


def apply_pahe_catalog(repo: Repository, payload: dict[str, Any]) -> bool:
    """Upsert Pahe link/seasons/episodes onto local anime matching mal_id."""
    mal_id = payload.get("mal_id")
    if not mal_id:
        return False
    entry = None
    for e in repo.list_user_anime():
        if e.mal_id == int(mal_id):
            entry = e
            break
    if not entry:
        return False
    link_id = repo.upsert_pahe_link(
        entry.user_anime_id,
        pahe_session=str(payload.get("pahe_session") or ""),
        pahe_title=str(payload.get("pahe_title") or ""),
        poster=str(payload.get("poster") or ""),
    )
    seasons = payload.get("seasons") or []
    season_meta = [
        {
            "pahe_session": s.get("pahe_session"),
            "label": s.get("label") or "",
            "year": s.get("year"),
            "episode_count": s.get("episode_count") or len(s.get("episodes") or []),
            "sort_order": s.get("sort_order") or i,
        }
        for i, s in enumerate(seasons)
        if s.get("pahe_session")
    ]
    season_ids = repo.replace_pahe_seasons(link_id, season_meta)
    for season_id, s in zip(season_ids, seasons, strict=False):
        eps = s.get("episodes") or []
        if eps:
            repo.replace_pahe_episodes(season_id, eps)
            repo.conn.execute(
                "UPDATE anime_pahe_season SET episode_count=? WHERE id=?",
                (len(eps), season_id),
            )
    if payload.get("synced_at"):
        repo.conn.execute(
            "UPDATE anime_pahe_link SET synced_at=? WHERE id=?",
            (payload["synced_at"], link_id),
        )
    repo.conn.commit()
    return True
