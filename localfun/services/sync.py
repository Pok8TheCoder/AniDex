from __future__ import annotations

from datetime import datetime

from localfun.db.repositories import Repository
from localfun.services.mal_api import MalClient, MalApiError
from localfun.services.mal_xml import export_mal_xml, parse_mal_xml


def import_xml_into_repo(repo: Repository, path: str) -> tuple[int, int]:
    """Merge MAL XML into local DB. Returns (imported, updated)."""
    rows = parse_mal_xml(path)
    imported = 0
    updated = 0
    for row in rows:
        existing = repo.get_anime_by_mal_id(row.mal_id)
        anime_id = repo.upsert_anime(
            mal_id=row.mal_id,
            title=row.title or f"MAL #{row.mal_id}",
            episodes=row.episodes,
        )
        ua = repo.conn.execute(
            "SELECT id, updated_at FROM user_anime WHERE anime_id=?",
            (anime_id,),
        ).fetchone()
        if ua:
            # Prefer newer timestamps when both exist
            if row.updated_at and ua["updated_at"]:
                if _ts(row.updated_at) < _ts(ua["updated_at"]):
                    continue
            updated += 1
        else:
            imported += 1
        repo.set_user_anime(
            anime_id,
            list_status=row.list_status,
            progress=row.progress,
            score=row.score,
            mal_synced_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        )
    return imported, updated


def export_repo_to_xml(repo: Repository, path: str) -> str:
    profile = repo.get_profile()
    entries = []
    for e in repo.list_user_anime():
        entries.append(
            {
                "mal_id": e.mal_id,
                "title": e.title,
                "episodes": e.episodes,
                "list_status": e.list_status,
                "progress": e.progress,
                "score": e.score,
                "updated_at": e.updated_at,
            }
        )
    export_mal_xml(entries, username=profile.mal_username or profile.display_name or "LocalFun", path=path)
    return path


def pull_mal_list(repo: Repository, client: MalClient) -> int:
    """Pull remote MAL list into local DB. Remote wins on conflict."""
    items = client.get_user_animelist("@me")
    count = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for item in items:
        node = item.get("node") or {}
        ls = item.get("list_status") or {}
        mal_id = int(node["id"])
        alts = node.get("alternative_titles") or {}
        pic = node.get("main_picture") or {}
        anime_id = repo.upsert_anime(
            mal_id=mal_id,
            title=node.get("title") or f"MAL #{mal_id}",
            title_english=alts.get("en"),
            media_type=node.get("media_type"),
            airing_status=node.get("status"),
            episodes=node.get("num_episodes"),
            mean_score=node.get("mean"),
            cover_url=pic.get("large") or pic.get("medium"),
            synopsis=node.get("synopsis"),
            cached=node,
        )
        repo.set_user_anime(
            anime_id,
            list_status=ls.get("status") or "plan_to_watch",
            progress=int(ls.get("num_episodes_watched") or 0),
            score=int(ls.get("score") or 0),
            mal_synced_at=now,
        )
        count += 1
    try:
        me = client.get_me()
        repo.update_profile(mal_username=me.get("name") or "")
    except MalApiError:
        pass
    return count


def push_local_to_mal(repo: Repository, client: MalClient) -> tuple[int, int]:
    """Push local list statuses to MAL. Returns (ok, failed)."""
    ok = 0
    failed = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for e in repo.list_user_anime():
        if not e.mal_id:
            continue
        try:
            client.update_list_status(
                e.mal_id,
                status=e.list_status,
                score=e.score,
                num_watched_episodes=e.progress,
            )
            repo.update_user_anime_fields(e.user_anime_id, mal_synced_at=now)
            ok += 1
        except MalApiError:
            failed += 1
    return ok, failed


def _ts(value: str) -> float:
    value = value.strip()
    if value.isdigit():
        return float(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.replace("Z", "+0000"), fmt).timestamp()
        except ValueError:
            continue
    return 0.0
