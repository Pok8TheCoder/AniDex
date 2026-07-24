"""AnimePahe search / release catalog via the Playwright browser session."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from anidex.db import open_repo
from anidex.services.pahe_browser import PAHE_HOME, browser_api_json

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


@dataclass
class PaheSearchHit:
    session: str
    title: str
    type: str
    year: int | None
    episodes: int
    poster: str
    status: str = ""


def play_url(anime_session: str, episode_session: str) -> str:
    base = PAHE_HOME.rstrip("/")
    return f"{base}/play/{anime_session}/{episode_session}"


def _norm(text: str) -> str:
    t = text.casefold()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _base_title(title: str) -> str:
    return _norm(_SEASON_TAIL.sub("", title.strip()))


def _score_title(query: str, candidate: str) -> float:
    q = _norm(query)
    c = _norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 100.0
    qb = _base_title(query)
    cb = _base_title(candidate)
    if qb and qb == cb:
        return 90.0
    if q in c or c in q:
        return 70.0 + min(len(q), len(c)) / max(len(q), len(c)) * 10
    if qb and (qb in cb or cb in qb):
        return 60.0
    qw = set(q.split())
    cw = set(c.split())
    if not qw:
        return 0.0
    overlap = len(qw & cw) / len(qw)
    return overlap * 50.0


def search_anime(query: str) -> list[PaheSearchHit]:
    q = query.strip()
    if not q:
        return []
    data = browser_api_json(f"/api?m=search&q={quote(q)}")
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    hits: list[PaheSearchHit] = []
    for row in rows:
        session = str(row.get("session") or "").strip()
        title = str(row.get("title") or "").strip()
        if not session or not title:
            continue
        year = row.get("year")
        try:
            year_i = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            year_i = None
        try:
            eps = int(row.get("episodes") or 0)
        except (TypeError, ValueError):
            eps = 0
        hits.append(
            PaheSearchHit(
                session=session,
                title=title,
                type=str(row.get("type") or ""),
                year=year_i,
                episodes=eps,
                poster=str(row.get("poster") or ""),
                status=str(row.get("status") or ""),
            )
        )
    return hits


def fetch_release_page(session: str, page: int = 1) -> dict[str, Any]:
    path = f"/api?m=release&id={session}&sort=episode_asc&page={page}"
    data = browser_api_json(path)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected release API response")
    return data


def fetch_all_releases(session: str) -> list[dict[str, Any]]:
    first = fetch_release_page(session, 1)
    rows = list(first.get("data") or [])
    try:
        last_page = int(first.get("last_page") or 1)
    except (TypeError, ValueError):
        last_page = 1
    for page in range(2, last_page + 1):
        data = fetch_release_page(session, page)
        rows.extend(data.get("data") or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        ep_session = str(row.get("session") or "").strip()
        if not ep_session:
            continue
        try:
            episode = float(row.get("episode"))
        except (TypeError, ValueError):
            continue
        ep2 = row.get("episode2")
        try:
            episode2 = float(ep2) if ep2 not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            episode2 = None
        out.append(
            {
                "episode": episode,
                "episode2": episode2,
                "episode_session": ep_session,
                "title": str(row.get("title") or ""),
                "snapshot": str(row.get("snapshot") or ""),
                "duration": str(row.get("duration") or ""),
                "disc": str(row.get("disc") or ""),
            }
        )
    out.sort(key=lambda r: (r["episode"], r["episode_session"]))
    return out


def pick_best_hit(query: str, hits: list[PaheSearchHit]) -> PaheSearchHit | None:
    if not hits:
        return None
    ranked = sorted(hits, key=lambda h: _score_title(query, h.title), reverse=True)
    best = ranked[0]
    if _score_title(query, best.title) < 20:
        return None
    return best


def related_season_hits(primary: PaheSearchHit, hits: list[PaheSearchHit]) -> list[PaheSearchHit]:
    base = _base_title(primary.title)
    related = [h for h in hits if _base_title(h.title) == base or base in _base_title(h.title)]
    if not related:
        related = [primary]
    # Deduplicate by session, keep primary first
    seen: set[str] = set()
    ordered: list[PaheSearchHit] = []
    for h in [primary] + sorted(related, key=lambda x: (x.year or 0, x.title)):
        if h.session in seen:
            continue
        seen.add(h.session)
        ordered.append(h)
    return ordered


def _season_label(hit: PaheSearchHit, index: int) -> str:
    m = re.search(
        r"(Season\s+\d+|S\d+\b|\d+(?:st|nd|rd|th)\s+Season|Part\s+\d+)",
        hit.title,
        re.I,
    )
    if m:
        return m.group(1)
    if index == 0 and len(_SEASON_TAIL.findall(hit.title)) == 0:
        return "Season 1"
    return hit.title


def link_anime_to_pahe(
    user_anime_id: int,
    query: str,
    *,
    chosen_session: str | None = None,
) -> dict[str, Any]:
    """Search Pahe, pick best (or chosen) match, cache seasons + episodes.

    Returns summary dict with link_id, pahe_title, season_count, episode_count.
    """
    hits = search_anime(query)
    if not hits:
        raise RuntimeError(f"No AnimePahe results for “{query}”.")

    if chosen_session:
        primary = next((h for h in hits if h.session == chosen_session), None)
        if primary is None:
            # Chosen from a prior search list not in this response — synthesize
            primary = PaheSearchHit(
                session=chosen_session,
                title=query,
                type="",
                year=None,
                episodes=0,
                poster="",
            )
            seasons = [primary]
        else:
            seasons = related_season_hits(primary, hits)
    else:
        primary = pick_best_hit(query, hits)
        if primary is None:
            primary = hits[0]
        seasons = related_season_hits(primary, hits)

    conn, repo = open_repo()
    try:
        link_id = repo.upsert_pahe_link(
            user_anime_id,
            pahe_session=primary.session,
            pahe_title=primary.title,
            poster=primary.poster,
        )
        season_rows: list[dict[str, Any]] = []
        for i, hit in enumerate(seasons):
            season_rows.append(
                {
                    "pahe_session": hit.session,
                    "label": _season_label(hit, i),
                    "year": hit.year,
                    "episode_count": hit.episodes,
                    "sort_order": i,
                }
            )
        season_ids = repo.replace_pahe_seasons(link_id, season_rows)

        total_eps = 0
        for season_id, hit in zip(season_ids, seasons, strict=True):
            episodes = fetch_all_releases(hit.session)
            repo.replace_pahe_episodes(season_id, episodes)
            total_eps += len(episodes)
            repo.conn.execute(
                "UPDATE anime_pahe_season SET episode_count=? WHERE id=?",
                (len(episodes), season_id),
            )
        repo.conn.commit()
        return {
            "link_id": link_id,
            "pahe_title": primary.title,
            "pahe_session": primary.session,
            "season_count": len(seasons),
            "episode_count": total_eps,
            "hits": hits,
        }
    finally:
        conn.close()


def refresh_pahe_episodes(user_anime_id: int) -> dict[str, Any]:
    """Re-fetch episode lists for an existing link."""
    conn, repo = open_repo()
    try:
        link = repo.get_pahe_link(user_anime_id)
        if not link:
            raise RuntimeError("Anime is not linked to AnimePahe yet.")
        seasons = repo.list_pahe_seasons(link.id)
        total = 0
        for season in seasons:
            episodes = fetch_all_releases(season.pahe_session)
            repo.replace_pahe_episodes(season.id, episodes)
            repo.conn.execute(
                "UPDATE anime_pahe_season SET episode_count=? WHERE id=?",
                (len(episodes), season.id),
            )
            total += len(episodes)
        repo.conn.execute(
            "UPDATE anime_pahe_link SET synced_at=datetime('now') WHERE id=?",
            (link.id,),
        )
        repo.conn.commit()
        return {
            "link_id": link.id,
            "pahe_title": link.pahe_title,
            "season_count": len(seasons),
            "episode_count": total,
        }
    finally:
        conn.close()
