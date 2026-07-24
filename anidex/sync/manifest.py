"""Build and apply sync manifests / entity batches."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from anidex.db.repositories import Repository
from anidex.db.schema import open_repo
from anidex.sync import APP_NAME, PROTOCOL_VERSION
from anidex.sync import merge as merge_mod
from anidex.sync.token import ensure_sync_identity


def _file_sha256(path: Path, *, limit: int = 0) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        if limit > 0:
            h.update(f.read(limit))
        else:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    return h.hexdigest()


def build_manifest(repo: Repository | None = None) -> dict[str, Any]:
    own = repo is None
    conn = None
    if own:
        conn, repo = open_repo()
    assert repo is not None
    try:
        ident = ensure_sync_identity()
        anime_entries = []
        for e in repo.list_user_anime():
            if not e.mal_id:
                continue
            key = f"mal:{e.mal_id}"
            anime_entries.append(
                {
                    "key": key,
                    "mal_id": e.mal_id,
                    "updated_at": e.updated_at,
                    "progress": e.progress,
                    "list_status": e.list_status,
                }
            )

        manga_entries = []
        for e in repo.list_user_manga():
            if not e.mangadex_id:
                continue
            key = f"md:{e.mangadex_id}"
            manga_entries.append(
                {
                    "key": key,
                    "mangadex_id": e.mangadex_id,
                    "updated_at": e.updated_at,
                    "progress": e.progress,
                    "list_status": e.list_status,
                }
            )

        media = []
        for m in repo.list_all_media():
            ua = m.get("user_anime_id")
            entry = repo.get_user_anime(ua) if ua else None
            mal_id = entry.mal_id if entry else m.get("mal_id")
            if not mal_id:
                continue
            ep = m.get("episode")
            sess = ""
            # look up session from DB row if needed
            row = repo.conn.execute(
                "SELECT path, pahe_episode_session, bytes FROM local_media WHERE id=?",
                (m["id"],),
            ).fetchone()
            if not row:
                continue
            path = Path(row["path"])
            sess = row["pahe_episode_session"] or ""
            sha = ""
            if path.is_file():
                try:
                    sha = _file_sha256(path)
                except OSError:
                    sha = ""
            key = f"media:{mal_id}:{ep}:{sess}"
            media.append(
                {
                    "key": key,
                    "mal_id": int(mal_id),
                    "episode": ep,
                    "pahe_episode_session": sess,
                    "bytes": int(row["bytes"] or 0),
                    "sha256": sha,
                    "label": m.get("label") or "",
                    "filename": path.name,
                    "local_media_id": m["id"],
                }
            )

        offline = []
        for e in repo.list_user_manga():
            if not e.mangadex_id:
                continue
            for o in repo.list_offline_chapters(e.user_manga_id):
                key = f"off:{e.mangadex_id}:{o.chapter_id}"
                offline.append(
                    {
                        "key": key,
                        "mangadex_id": e.mangadex_id,
                        "chapter_id": o.chapter_id,
                        "chapter_no": o.chapter_no,
                        "title": o.title,
                        "pages": o.pages,
                        "bytes": o.bytes,
                        "quality": o.quality,
                        "updated_at": o.created_at,
                        "user_manga_id": e.user_manga_id,
                    }
                )

        positions = []
        for e in repo.list_user_manga():
            if not e.mangadex_id:
                continue
            for cid, page in repo.list_read_positions(e.user_manga_id).items():
                row = repo.conn.execute(
                    """
                    SELECT updated_at FROM manga_read_position
                    WHERE user_manga_id=? AND chapter_id=?
                    """,
                    (e.user_manga_id, cid),
                ).fetchone()
                positions.append(
                    {
                        "key": f"pos:{e.mangadex_id}:{cid}",
                        "mangadex_id": e.mangadex_id,
                        "chapter_id": cid,
                        "page_index": page,
                        "updated_at": row["updated_at"] if row else "",
                    }
                )

        tombs = []
        for r in repo.conn.execute(
            "SELECT entity_type, entity_key, deleted_at, updated_at FROM sync_tombstone"
        ).fetchall():
            tombs.append(
                {
                    "key": f"tomb:{r['entity_type']}:{r['entity_key']}",
                    "entity_type": r["entity_type"],
                    "entity_key": r["entity_key"],
                    "deleted_at": r["deleted_at"],
                    "updated_at": r["updated_at"],
                }
            )

        return {
            "app": APP_NAME,
            "protocol": PROTOCOL_VERSION,
            "device_id": ident["device_id"],
            "anime": anime_entries,
            "manga": manga_entries,
            "media": media,
            "offline": offline,
            "positions": positions,
            "tombstones": tombs,
        }
    finally:
        if own and conn is not None:
            conn.close()


def export_entities(keys: list[str]) -> dict[str, Any]:
    """Export full payloads for requested keys."""
    conn, repo = open_repo()
    try:
        anime_by_mal = {e.mal_id: e for e in repo.list_user_anime() if e.mal_id}
        manga_by_md = {e.mangadex_id: e for e in repo.list_user_manga() if e.mangadex_id}
        batches: dict[str, Any] = {
            "anime": [],
            "manga": [],
            "positions": [],
            "offline_meta": [],
            "media_meta": [],
            "tombstones": [],
        }
        for key in keys:
            if key.startswith("mal:"):
                mal_id = int(key.split(":", 1)[1])
                e = anime_by_mal.get(mal_id)
                if not e:
                    continue
                batches["anime"].append(_anime_payload(e))
            elif key.startswith("md:"):
                mid = key.split(":", 1)[1]
                e = manga_by_md.get(mid)
                if not e:
                    continue
                batches["manga"].append(_manga_payload(e))
            elif key.startswith("pos:"):
                _, mid, cid = key.split(":", 2)
                e = manga_by_md.get(mid)
                if not e:
                    continue
                page = repo.get_read_position(e.user_manga_id, cid)
                if page is None:
                    continue
                row = repo.conn.execute(
                    """
                    SELECT updated_at FROM manga_read_position
                    WHERE user_manga_id=? AND chapter_id=?
                    """,
                    (e.user_manga_id, cid),
                ).fetchone()
                batches["positions"].append(
                    {
                        "mangadex_id": mid,
                        "chapter_id": cid,
                        "page_index": page,
                        "updated_at": row["updated_at"] if row else "",
                    }
                )
            elif key.startswith("off:"):
                _, mid, cid = key.split(":", 2)
                e = manga_by_md.get(mid)
                if not e:
                    continue
                off = repo.get_offline_chapter(e.user_manga_id, cid)
                if not off:
                    continue
                batches["offline_meta"].append(
                    {
                        "mangadex_id": mid,
                        "chapter_id": cid,
                        "chapter_no": off.chapter_no,
                        "title": off.title,
                        "pages": off.pages,
                        "bytes": off.bytes,
                        "quality": off.quality,
                        "updated_at": off.created_at,
                    }
                )
            elif key.startswith("media:"):
                parts = key.split(":")
                # media:mal_id:episode:session
                mal_id = int(parts[1])
                ep = parts[2] if len(parts) > 2 else ""
                sess = parts[3] if len(parts) > 3 else ""
                e = anime_by_mal.get(mal_id)
                if not e:
                    continue
                for m in repo.list_media(e.user_anime_id):
                    if str(m.episode) == str(ep) or (
                        ep in ("", "None", "null") and m.episode is None
                    ):
                        if sess and m.pahe_episode_session != sess:
                            continue
                        path = Path(m.path)
                        sha = _file_sha256(path) if path.is_file() else ""
                        batches["media_meta"].append(
                            {
                                "key": key,
                                "mal_id": mal_id,
                                "episode": m.episode,
                                "pahe_episode_session": m.pahe_episode_session,
                                "bytes": m.bytes,
                                "sha256": sha,
                                "label": m.label,
                                "filename": path.name,
                                "title": e.title_english or e.title,
                            }
                        )
                        break
            elif key.startswith("tomb:"):
                _, etype, ekey = key.split(":", 2)
                row = repo.conn.execute(
                    """
                    SELECT entity_type, entity_key, deleted_at, updated_at
                    FROM sync_tombstone WHERE entity_type=? AND entity_key=?
                    """,
                    (etype, ekey),
                ).fetchone()
                if row:
                    batches["tombstones"].append(dict(row))
        return batches
    finally:
        conn.close()


def _anime_payload(e) -> dict[str, Any]:
    return {
        "mal_id": e.mal_id,
        "title": e.title,
        "title_english": e.title_english,
        "media_type": e.media_type,
        "airing_status": e.airing_status,
        "episodes": e.episodes,
        "mean_score": e.mean_score,
        "cover_url": e.cover_url,
        "synopsis": e.synopsis,
        "list_status": e.list_status,
        "progress": e.progress,
        "score": e.score,
        "notes": e.notes,
        "local_folder": e.local_folder,
        "updated_at": e.updated_at,
    }


def _manga_payload(e) -> dict[str, Any]:
    return {
        "mangadex_id": e.mangadex_id,
        "title": e.title,
        "title_english": e.title_english,
        "status": e.status,
        "year": e.year,
        "cover_url": e.cover_url,
        "synopsis": e.synopsis,
        "list_status": e.list_status,
        "progress": e.progress,
        "score": e.score,
        "notes": e.notes,
        "local_folder": e.local_folder,
        "updated_at": e.updated_at,
    }


def apply_batches(batches: dict[str, Any]) -> dict[str, int]:
    """Merge remote batches into local DB. Returns counts."""
    conn, repo = open_repo()
    counts = {
        "anime": 0,
        "manga": 0,
        "positions": 0,
        "offline_meta": 0,
        "media_meta": 0,
        "tombstones": 0,
    }
    try:
        for tomb in batches.get("tombstones") or []:
            repo.conn.execute(
                """
                INSERT INTO sync_tombstone (entity_type, entity_key, deleted_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_key) DO UPDATE SET
                  deleted_at=excluded.deleted_at,
                  updated_at=excluded.updated_at
                """,
                (
                    tomb["entity_type"],
                    tomb["entity_key"],
                    tomb.get("deleted_at") or "",
                    tomb.get("updated_at") or "",
                ),
            )
            counts["tombstones"] += 1

        for remote in batches.get("anime") or []:
            mal_id = remote.get("mal_id")
            if not mal_id:
                continue
            key = f"mal:{mal_id}"
            tomb = repo.conn.execute(
                "SELECT deleted_at FROM sync_tombstone WHERE entity_type='anime' AND entity_key=?",
                (key,),
            ).fetchone()
            if tomb and merge_mod.tombstone_wins(
                {"deleted_at": tomb["deleted_at"]}, remote.get("updated_at") or ""
            ):
                continue
            anime_id = repo.upsert_anime(
                mal_id=mal_id,
                title=remote.get("title") or "Unknown",
                title_english=remote.get("title_english"),
                media_type=remote.get("media_type"),
                airing_status=remote.get("airing_status"),
                episodes=remote.get("episodes"),
                mean_score=remote.get("mean_score"),
                cover_url=remote.get("cover_url"),
                synopsis=remote.get("synopsis"),
            )
            existing = None
            for e in repo.list_user_anime():
                if e.mal_id == mal_id:
                    existing = e
                    break
            if existing:
                merged = merge_mod.merge_list_entry(
                    {
                        "list_status": existing.list_status,
                        "progress": existing.progress,
                        "score": existing.score,
                        "notes": existing.notes,
                        "local_folder": existing.local_folder,
                        "updated_at": existing.updated_at,
                    },
                    remote,
                )
                repo.update_user_anime_fields(
                    existing.user_anime_id,
                    list_status=merged["list_status"],
                    progress=merged["progress"],
                    score=merged["score"],
                    notes=merged.get("notes") or "",
                    local_folder=merged.get("local_folder") or "",
                )
            else:
                repo.set_user_anime(
                    anime_id,
                    list_status=remote.get("list_status") or "plan_to_watch",
                    progress=int(remote.get("progress") or 0),
                    score=int(remote.get("score") or 0),
                    notes=remote.get("notes") or "",
                    local_folder=remote.get("local_folder") or "",
                )
            counts["anime"] += 1

        for remote in batches.get("manga") or []:
            mid = remote.get("mangadex_id")
            if not mid:
                continue
            key = f"md:{mid}"
            tomb = repo.conn.execute(
                "SELECT deleted_at FROM sync_tombstone WHERE entity_type='manga' AND entity_key=?",
                (key,),
            ).fetchone()
            if tomb and merge_mod.tombstone_wins(
                {"deleted_at": tomb["deleted_at"]}, remote.get("updated_at") or ""
            ):
                continue
            manga_id = repo.upsert_manga(
                mangadex_id=mid,
                title=remote.get("title") or "Unknown",
                title_english=remote.get("title_english"),
                status=remote.get("status"),
                year=remote.get("year"),
                cover_url=remote.get("cover_url"),
                synopsis=remote.get("synopsis"),
            )
            existing = None
            for e in repo.list_user_manga():
                if e.mangadex_id == mid:
                    existing = e
                    break
            if existing:
                merged = merge_mod.merge_list_entry(
                    {
                        "list_status": existing.list_status,
                        "progress": existing.progress,
                        "score": existing.score,
                        "notes": existing.notes,
                        "local_folder": existing.local_folder,
                        "updated_at": existing.updated_at,
                    },
                    remote,
                )
                repo.update_user_manga_fields(
                    existing.user_manga_id,
                    list_status=merged["list_status"],
                    progress=merged["progress"],
                    score=merged["score"],
                    notes=merged.get("notes") or "",
                    local_folder=merged.get("local_folder") or "",
                )
            else:
                repo.set_user_manga(
                    manga_id,
                    list_status=remote.get("list_status") or "plan_to_read",
                    progress=int(remote.get("progress") or 0),
                    score=int(remote.get("score") or 0),
                    notes=remote.get("notes") or "",
                    local_folder=remote.get("local_folder") or "",
                )
            counts["manga"] += 1

        manga_by_md = {e.mangadex_id: e for e in repo.list_user_manga() if e.mangadex_id}
        for remote in batches.get("positions") or []:
            mid = remote.get("mangadex_id")
            cid = remote.get("chapter_id")
            e = manga_by_md.get(mid)
            if not e or not cid:
                continue
            local_page = repo.get_read_position(e.user_manga_id, cid)
            local = {
                "page_index": local_page or 0,
                "updated_at": "",
                "mangadex_id": mid,
                "chapter_id": cid,
            }
            if local_page is not None:
                row = repo.conn.execute(
                    """
                    SELECT updated_at FROM manga_read_position
                    WHERE user_manga_id=? AND chapter_id=?
                    """,
                    (e.user_manga_id, cid),
                ).fetchone()
                local["updated_at"] = row["updated_at"] if row else ""
            merged = merge_mod.merge_read_position(local, remote)
            repo.set_read_position(e.user_manga_id, cid, merged["page_index"])
            counts["positions"] += 1

        # offline_meta / media_meta recorded for client media fetch; counts only
        counts["offline_meta"] = len(batches.get("offline_meta") or [])
        counts["media_meta"] = len(batches.get("media_meta") or [])
        repo.conn.commit()
        return counts
    finally:
        conn.close()


def diff_manifest(local: dict[str, Any], remote: dict[str, Any]) -> list[str]:
    """Keys to pull from remote (newer or missing locally)."""
    need: list[str] = []

    def map_entries(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {i["key"]: i for i in items if i.get("key")}

    for section in ("anime", "manga", "positions", "offline", "media", "tombstones"):
        loc = map_entries(local.get(section) or [])
        rem = map_entries(remote.get(section) or [])
        for key, r in rem.items():
            l = loc.get(key)
            if not l:
                need.append(key)
                continue
            if section == "media":
                if (r.get("sha256") or "") and r.get("sha256") != (l.get("sha256") or ""):
                    need.append(key)
                elif int(r.get("bytes") or 0) > int(l.get("bytes") or 0):
                    need.append(key)
            elif section in ("anime", "manga", "positions", "offline", "tombstones"):
                if (r.get("updated_at") or "") > (l.get("updated_at") or ""):
                    need.append(key)
                elif section in ("anime", "manga") and int(r.get("progress") or 0) > int(
                    l.get("progress") or 0
                ):
                    need.append(key)
                elif section == "positions" and int(r.get("page_index") or 0) > int(
                    l.get("page_index") or 0
                ):
                    need.append(key)
    return need
