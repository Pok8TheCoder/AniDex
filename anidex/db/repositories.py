from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _norm_title(text: str) -> str:
    t = text.casefold()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@dataclass
class Profile:
    display_name: str
    onboarded: bool
    mal_username: str
    mal_client_id: str


@dataclass
class AnimeEntry:
    user_anime_id: int
    anime_id: int
    mal_id: int | None
    title: str
    title_english: str | None
    media_type: str | None
    airing_status: str | None
    episodes: int | None
    mean_score: float | None
    cover_url: str | None
    synopsis: str | None
    list_status: str
    progress: int
    score: int
    notes: str
    local_folder: str
    updated_at: str
    mal_synced_at: str | None


@dataclass
class MangaEntry:
    user_manga_id: int
    manga_id: int
    mangadex_id: str | None
    title: str
    title_english: str | None
    status: str | None
    year: int | None
    cover_url: str | None
    synopsis: str | None
    list_status: str
    progress: int
    score: int
    notes: str
    local_folder: str
    updated_at: str


@dataclass
class PaheLink:
    id: int
    user_anime_id: int
    pahe_session: str
    pahe_title: str
    poster: str
    synced_at: str


@dataclass
class PaheSeason:
    id: int
    link_id: int
    pahe_session: str
    label: str
    year: int | None
    episode_count: int
    sort_order: int


@dataclass
class PaheEpisode:
    id: int
    season_id: int
    episode: float
    episode2: float | None
    episode_session: str
    title: str
    snapshot: str
    duration: str
    disc: str


@dataclass
class LocalMediaEntry:
    id: int
    user_anime_id: int | None
    pahe_episode_session: str
    episode: float | None
    path: str
    label: str
    bytes: int
    created_at: str


@dataclass
class OfflineChapter:
    id: int
    user_manga_id: int
    chapter_id: str
    chapter_no: str
    title: str
    pages: int
    path: str
    quality: str
    bytes: int
    created_at: str


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- profile ---
    def get_profile(self) -> Profile:
        row = self.conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        return Profile(
            display_name=row["display_name"] or "",
            onboarded=bool(row["onboarded"]),
            mal_username=row["mal_username"] or "",
            mal_client_id=row["mal_client_id"] or "",
        )

    def update_profile(
        self,
        *,
        display_name: str | None = None,
        onboarded: bool | None = None,
        mal_username: str | None = None,
        mal_client_id: str | None = None,
    ) -> Profile:
        p = self.get_profile()
        dn = p.display_name if display_name is None else display_name
        ob = p.onboarded if onboarded is None else onboarded
        mu = p.mal_username if mal_username is None else mal_username
        mc = p.mal_client_id if mal_client_id is None else mal_client_id
        self.conn.execute(
            """
            UPDATE profile SET display_name=?, onboarded=?, mal_username=?,
            mal_client_id=?, updated_at=? WHERE id=1
            """,
            (dn, int(ob), mu, mc, _now()),
        )
        self.conn.commit()
        return self.get_profile()

    # --- anime cache ---
    def upsert_anime(
        self,
        *,
        mal_id: int | None,
        title: str,
        title_english: str | None = None,
        media_type: str | None = None,
        airing_status: str | None = None,
        episodes: int | None = None,
        mean_score: float | None = None,
        cover_url: str | None = None,
        synopsis: str | None = None,
        cached: dict[str, Any] | None = None,
    ) -> int:
        cached_json = json.dumps(cached) if cached else None
        if mal_id is not None:
            existing = self.conn.execute(
                "SELECT id FROM anime WHERE mal_id = ?", (mal_id,)
            ).fetchone()
            if existing:
                self.conn.execute(
                    """
                    UPDATE anime SET title=?, title_english=?, media_type=?,
                    airing_status=?, episodes=?, mean_score=?, cover_url=?,
                    synopsis=?, cached_json=?, updated_at=? WHERE id=?
                    """,
                    (
                        title,
                        title_english,
                        media_type,
                        airing_status,
                        episodes,
                        mean_score,
                        cover_url,
                        synopsis,
                        cached_json,
                        _now(),
                        existing["id"],
                    ),
                )
                self.conn.commit()
                return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO anime (mal_id, title, title_english, media_type, airing_status,
            episodes, mean_score, cover_url, synopsis, cached_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mal_id,
                title,
                title_english,
                media_type,
                airing_status,
                episodes,
                mean_score,
                cover_url,
                synopsis,
                cached_json,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_anime_by_mal_id(self, mal_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM anime WHERE mal_id = ?", (mal_id,)
        ).fetchone()

    # --- user anime ---
    def set_user_anime(
        self,
        anime_id: int,
        *,
        list_status: str = "plan_to_watch",
        progress: int = 0,
        score: int = 0,
        notes: str = "",
        local_folder: str = "",
        mal_synced_at: str | None = None,
        touch: bool = True,
    ) -> int:
        existing = self.conn.execute(
            "SELECT id FROM user_anime WHERE anime_id = ?", (anime_id,)
        ).fetchone()
        updated = _now() if touch else None
        if existing:
            if touch:
                self.conn.execute(
                    """
                    UPDATE user_anime SET list_status=?, progress=?, score=?, notes=?,
                    local_folder=?, updated_at=?, mal_synced_at=COALESCE(?, mal_synced_at)
                    WHERE id=?
                    """,
                    (
                        list_status,
                        progress,
                        score,
                        notes,
                        local_folder,
                        updated,
                        mal_synced_at,
                        existing["id"],
                    ),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE user_anime SET list_status=?, progress=?, score=?, notes=?,
                    local_folder=?, mal_synced_at=COALESCE(?, mal_synced_at)
                    WHERE id=?
                    """,
                    (
                        list_status,
                        progress,
                        score,
                        notes,
                        local_folder,
                        mal_synced_at,
                        existing["id"],
                    ),
                )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO user_anime (anime_id, list_status, progress, score, notes,
            local_folder, updated_at, mal_synced_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                anime_id,
                list_status,
                progress,
                score,
                notes,
                local_folder,
                _now(),
                mal_synced_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_user_anime_fields(
        self,
        user_anime_id: int,
        **fields: Any,
    ) -> None:
        allowed = {
            "list_status",
            "progress",
            "score",
            "notes",
            "local_folder",
            "mal_synced_at",
        }
        sets = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        vals.append(_now())
        vals.append(user_anime_id)
        self.conn.execute(
            f"UPDATE user_anime SET {', '.join(sets)} WHERE id=?",
            vals,
        )
        self.conn.commit()

    def find_user_anime_by_title(self, title: str) -> AnimeEntry | None:
        """Best-effort match of a list entry to an AnimePahe / MAL title."""
        needle = _norm_title(title)
        if not needle:
            return None
        entries = self.list_user_anime()
        best: AnimeEntry | None = None
        best_score = 0.0
        for e in entries:
            for candidate in (e.title_english, e.title):
                if not candidate:
                    continue
                hay = _norm_title(candidate)
                if not hay:
                    continue
                if needle == hay:
                    return e
                if needle in hay or hay in needle:
                    score = min(len(needle), len(hay)) / max(len(needle), len(hay))
                    if score > best_score:
                        best_score = score
                        best = e
        return best if best_score >= 0.55 else None

    def ensure_local_anime(
        self,
        title: str,
        *,
        episodes: int | None = None,
        cover_url: str | None = None,
        pahe_session: str = "",
        list_status: str = "watching",
        progress: int = 0,
        local_folder: str = "",
    ) -> AnimeEntry:
        """Create or reuse a local list entry for an AnimePahe title (no MAL required)."""
        existing = self.find_user_anime_by_title(title)
        if existing:
            fields: dict[str, Any] = {}
            if local_folder and not existing.local_folder:
                fields["local_folder"] = local_folder
            if progress > existing.progress:
                fields["progress"] = progress
                if existing.list_status == "plan_to_watch":
                    fields["list_status"] = "watching"
            if fields:
                self.update_user_anime_fields(existing.user_anime_id, **fields)
                refreshed = self.get_user_anime(existing.user_anime_id)
                return refreshed or existing
            return existing

        cached = {"animepahe_session": pahe_session} if pahe_session else None
        anime_id = self.upsert_anime(
            mal_id=None,
            title=title,
            title_english=title,
            episodes=episodes,
            cover_url=cover_url,
            cached=cached,
        )
        ua_id = self.set_user_anime(
            anime_id,
            list_status=list_status,
            progress=progress,
            local_folder=local_folder,
        )
        entry = self.get_user_anime(ua_id)
        assert entry is not None
        return entry

    def remove_user_anime(self, user_anime_id: int) -> None:
        self.conn.execute("DELETE FROM user_anime WHERE id=?", (user_anime_id,))
        self.conn.commit()

    def list_user_anime(self, status: str | None = None) -> list[AnimeEntry]:
        q = """
            SELECT ua.id AS user_anime_id, a.id AS anime_id, a.mal_id, a.title,
                   a.title_english, a.media_type, a.airing_status, a.episodes,
                   a.mean_score, a.cover_url, a.synopsis,
                   ua.list_status, ua.progress, ua.score, ua.notes, ua.local_folder,
                   ua.updated_at, ua.mal_synced_at
            FROM user_anime ua
            JOIN anime a ON a.id = ua.anime_id
        """
        params: tuple[Any, ...] = ()
        if status and status != "all":
            q += " WHERE ua.list_status = ?"
            params = (status,)
        q += " ORDER BY ua.updated_at DESC"
        rows = self.conn.execute(q, params).fetchall()
        return [self._anime_entry(r) for r in rows]

    def get_user_anime(self, user_anime_id: int) -> AnimeEntry | None:
        rows = self.conn.execute(
            """
            SELECT ua.id AS user_anime_id, a.id AS anime_id, a.mal_id, a.title,
                   a.title_english, a.media_type, a.airing_status, a.episodes,
                   a.mean_score, a.cover_url, a.synopsis,
                   ua.list_status, ua.progress, ua.score, ua.notes, ua.local_folder,
                   ua.updated_at, ua.mal_synced_at
            FROM user_anime ua
            JOIN anime a ON a.id = ua.anime_id
            WHERE ua.id = ?
            """,
            (user_anime_id,),
        ).fetchall()
        return self._anime_entry(rows[0]) if rows else None

    def count_user_anime_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT list_status, COUNT(*) AS c FROM user_anime GROUP BY list_status"
        ).fetchall()
        return {r["list_status"]: r["c"] for r in rows}

    @staticmethod
    def _anime_entry(r: sqlite3.Row) -> AnimeEntry:
        return AnimeEntry(
            user_anime_id=r["user_anime_id"],
            anime_id=r["anime_id"],
            mal_id=r["mal_id"],
            title=r["title"],
            title_english=r["title_english"],
            media_type=r["media_type"],
            airing_status=r["airing_status"],
            episodes=r["episodes"],
            mean_score=r["mean_score"],
            cover_url=r["cover_url"],
            synopsis=r["synopsis"],
            list_status=r["list_status"],
            progress=r["progress"],
            score=r["score"],
            notes=r["notes"] or "",
            local_folder=r["local_folder"] or "",
            updated_at=r["updated_at"],
            mal_synced_at=r["mal_synced_at"],
        )

    # --- manga ---
    def upsert_manga(
        self,
        *,
        mangadex_id: str | None,
        title: str,
        title_english: str | None = None,
        status: str | None = None,
        year: int | None = None,
        cover_url: str | None = None,
        synopsis: str | None = None,
        cached: dict[str, Any] | None = None,
    ) -> int:
        cached_json = json.dumps(cached) if cached else None
        if mangadex_id:
            existing = self.conn.execute(
                "SELECT id FROM manga WHERE mangadex_id = ?", (mangadex_id,)
            ).fetchone()
            if existing:
                self.conn.execute(
                    """
                    UPDATE manga SET title=?, title_english=?, status=?, year=?,
                    cover_url=?, synopsis=?, cached_json=?, updated_at=? WHERE id=?
                    """,
                    (
                        title,
                        title_english,
                        status,
                        year,
                        cover_url,
                        synopsis,
                        cached_json,
                        _now(),
                        existing["id"],
                    ),
                )
                self.conn.commit()
                return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO manga (mangadex_id, title, title_english, status, year,
            cover_url, synopsis, cached_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                mangadex_id,
                title,
                title_english,
                status,
                year,
                cover_url,
                synopsis,
                cached_json,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_user_manga(
        self,
        manga_id: int,
        *,
        list_status: str = "plan_to_read",
        progress: int = 0,
        score: int = 0,
        notes: str = "",
        local_folder: str = "",
    ) -> int:
        existing = self.conn.execute(
            "SELECT id FROM user_manga WHERE manga_id = ?", (manga_id,)
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE user_manga SET list_status=?, progress=?, score=?, notes=?,
                local_folder=?, updated_at=? WHERE id=?
                """,
                (
                    list_status,
                    progress,
                    score,
                    notes,
                    local_folder,
                    _now(),
                    existing["id"],
                ),
            )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO user_manga (manga_id, list_status, progress, score, notes,
            local_folder, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (manga_id, list_status, progress, score, notes, local_folder, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_user_manga_fields(self, user_manga_id: int, **fields: Any) -> None:
        allowed = {"list_status", "progress", "score", "notes", "local_folder"}
        sets = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        vals.append(_now())
        vals.append(user_manga_id)
        self.conn.execute(
            f"UPDATE user_manga SET {', '.join(sets)} WHERE id=?",
            vals,
        )
        self.conn.commit()

    def remove_user_manga(self, user_manga_id: int) -> None:
        self.conn.execute("DELETE FROM user_manga WHERE id=?", (user_manga_id,))
        self.conn.commit()

    def list_user_manga(self, status: str | None = None) -> list[MangaEntry]:
        q = """
            SELECT um.id AS user_manga_id, m.id AS manga_id, m.mangadex_id, m.title,
                   m.title_english, m.status, m.year, m.cover_url, m.synopsis,
                   um.list_status, um.progress, um.score, um.notes, um.local_folder,
                   um.updated_at
            FROM user_manga um
            JOIN manga m ON m.id = um.manga_id
        """
        params: tuple[Any, ...] = ()
        if status and status != "all":
            q += " WHERE um.list_status = ?"
            params = (status,)
        q += " ORDER BY um.updated_at DESC"
        rows = self.conn.execute(q, params).fetchall()
        return [self._manga_entry(r) for r in rows]

    def get_user_manga(self, user_manga_id: int) -> MangaEntry | None:
        rows = self.conn.execute(
            """
            SELECT um.id AS user_manga_id, m.id AS manga_id, m.mangadex_id, m.title,
                   m.title_english, m.status, m.year, m.cover_url, m.synopsis,
                   um.list_status, um.progress, um.score, um.notes, um.local_folder,
                   um.updated_at
            FROM user_manga um
            JOIN manga m ON m.id = um.manga_id
            WHERE um.id = ?
            """,
            (user_manga_id,),
        ).fetchall()
        return self._manga_entry(rows[0]) if rows else None

    def count_user_manga_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT list_status, COUNT(*) AS c FROM user_manga GROUP BY list_status"
        ).fetchall()
        return {r["list_status"]: r["c"] for r in rows}

    @staticmethod
    def _manga_entry(r: sqlite3.Row) -> MangaEntry:
        return MangaEntry(
            user_manga_id=r["user_manga_id"],
            manga_id=r["manga_id"],
            mangadex_id=r["mangadex_id"],
            title=r["title"],
            title_english=r["title_english"],
            status=r["status"],
            year=r["year"],
            cover_url=r["cover_url"],
            synopsis=r["synopsis"],
            list_status=r["list_status"],
            progress=r["progress"],
            score=r["score"],
            notes=r["notes"] or "",
            local_folder=r["local_folder"] or "",
            updated_at=r["updated_at"],
        )

    # --- AnimePahe catalog cache ---

    def get_pahe_link(self, user_anime_id: int) -> PaheLink | None:
        row = self.conn.execute(
            "SELECT * FROM anime_pahe_link WHERE user_anime_id = ?",
            (user_anime_id,),
        ).fetchone()
        return self._pahe_link(row) if row else None

    def upsert_pahe_link(
        self,
        user_anime_id: int,
        *,
        pahe_session: str,
        pahe_title: str,
        poster: str = "",
    ) -> int:
        existing = self.get_pahe_link(user_anime_id)
        if existing:
            self.conn.execute(
                """
                UPDATE anime_pahe_link
                SET pahe_session=?, pahe_title=?, poster=?, synced_at=?
                WHERE id=?
                """,
                (pahe_session, pahe_title, poster, _now(), existing.id),
            )
            self.conn.commit()
            return existing.id
        cur = self.conn.execute(
            """
            INSERT INTO anime_pahe_link (user_anime_id, pahe_session, pahe_title, poster, synced_at)
            VALUES (?,?,?,?,?)
            """,
            (user_anime_id, pahe_session, pahe_title, poster, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def clear_pahe_catalog(self, link_id: int) -> None:
        seasons = self.conn.execute(
            "SELECT id FROM anime_pahe_season WHERE link_id = ?", (link_id,)
        ).fetchall()
        for s in seasons:
            self.conn.execute(
                "DELETE FROM anime_pahe_episode WHERE season_id = ?", (s["id"],)
            )
        self.conn.execute(
            "DELETE FROM anime_pahe_season WHERE link_id = ?", (link_id,)
        )
        self.conn.commit()

    def replace_pahe_seasons(
        self,
        link_id: int,
        seasons: list[dict[str, Any]],
    ) -> list[int]:
        """Replace season rows. Each dict: pahe_session, label, year, episode_count, sort_order."""
        self.clear_pahe_catalog(link_id)
        ids: list[int] = []
        for s in seasons:
            cur = self.conn.execute(
                """
                INSERT INTO anime_pahe_season
                (link_id, pahe_session, label, year, episode_count, sort_order)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    link_id,
                    s["pahe_session"],
                    s.get("label") or "",
                    s.get("year"),
                    int(s.get("episode_count") or 0),
                    int(s.get("sort_order") or 0),
                ),
            )
            ids.append(int(cur.lastrowid))
        self.conn.commit()
        return ids

    def replace_pahe_episodes(
        self,
        season_id: int,
        episodes: list[dict[str, Any]],
    ) -> None:
        self.conn.execute(
            "DELETE FROM anime_pahe_episode WHERE season_id = ?", (season_id,)
        )
        for ep in episodes:
            self.conn.execute(
                """
                INSERT INTO anime_pahe_episode
                (season_id, episode, episode2, episode_session, title, snapshot, duration, disc)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    season_id,
                    float(ep["episode"]),
                    ep.get("episode2"),
                    ep["episode_session"],
                    ep.get("title") or "",
                    ep.get("snapshot") or "",
                    ep.get("duration") or "",
                    ep.get("disc") or "",
                ),
            )
        self.conn.commit()

    def list_pahe_seasons(self, link_id: int) -> list[PaheSeason]:
        rows = self.conn.execute(
            """
            SELECT * FROM anime_pahe_season
            WHERE link_id = ?
            ORDER BY sort_order ASC, year ASC, id ASC
            """,
            (link_id,),
        ).fetchall()
        return [self._pahe_season(r) for r in rows]

    def list_pahe_episodes(self, season_id: int) -> list[PaheEpisode]:
        rows = self.conn.execute(
            """
            SELECT * FROM anime_pahe_episode
            WHERE season_id = ?
            ORDER BY episode ASC, id ASC
            """,
            (season_id,),
        ).fetchall()
        return [self._pahe_episode(r) for r in rows]

    @staticmethod
    def _pahe_link(r: sqlite3.Row) -> PaheLink:
        return PaheLink(
            id=r["id"],
            user_anime_id=r["user_anime_id"],
            pahe_session=r["pahe_session"] or "",
            pahe_title=r["pahe_title"] or "",
            poster=r["poster"] or "",
            synced_at=r["synced_at"],
        )

    @staticmethod
    def _pahe_season(r: sqlite3.Row) -> PaheSeason:
        return PaheSeason(
            id=r["id"],
            link_id=r["link_id"],
            pahe_session=r["pahe_session"],
            label=r["label"] or "",
            year=r["year"],
            episode_count=int(r["episode_count"] or 0),
            sort_order=int(r["sort_order"] or 0),
        )

    @staticmethod
    def _pahe_episode(r: sqlite3.Row) -> PaheEpisode:
        return PaheEpisode(
            id=r["id"],
            season_id=r["season_id"],
            episode=float(r["episode"]),
            episode2=float(r["episode2"]) if r["episode2"] is not None else None,
            episode_session=r["episode_session"],
            title=r["title"] or "",
            snapshot=r["snapshot"] or "",
            duration=r["duration"] or "",
            disc=r["disc"] or "",
        )

    # --- Local media library ---

    def list_media(self, user_anime_id: int) -> list[LocalMediaEntry]:
        rows = self.conn.execute(
            """
            SELECT * FROM local_media
            WHERE user_anime_id = ?
            ORDER BY episode ASC, id ASC
            """,
            (user_anime_id,),
        ).fetchall()
        return [self._local_media(r) for r in rows]

    def list_all_media(self) -> list[dict[str, Any]]:
        """All downloaded anime files with title metadata for the Downloads tab."""
        rows = self.conn.execute(
            """
            SELECT m.*,
                   a.title AS anime_title,
                   a.title_english AS anime_title_english,
                   a.cover_url AS anime_cover_url,
                   a.mal_id AS anime_mal_id
            FROM local_media m
            LEFT JOIN user_anime ua ON ua.id = m.user_anime_id
            LEFT JOIN anime a ON a.id = ua.anime_id
            WHERE m.media_type = 'anime' OR m.user_anime_id IS NOT NULL
            ORDER BY m.created_at DESC, m.id DESC
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            title = r["anime_title_english"] or r["anime_title"] or r["label"] or "Anime"
            out.append(
                {
                    "id": int(r["id"]),
                    "user_anime_id": int(r["user_anime_id"]) if r["user_anime_id"] else None,
                    "episode": float(r["episode"]) if r["episode"] is not None else None,
                    "label": r["label"] or "",
                    "bytes": int(r["bytes"] or 0),
                    "created_at": r["created_at"] or "",
                    "title": title,
                    "cover_url": r["anime_cover_url"] or "",
                    "mal_id": int(r["anime_mal_id"]) if r["anime_mal_id"] else None,
                    "filename": Path(r["path"]).name if r["path"] else "",
                }
            )
        return out

    def get_media_for_episode(
        self,
        user_anime_id: int,
        pahe_episode_session: str,
    ) -> LocalMediaEntry | None:
        row = self.conn.execute(
            """
            SELECT * FROM local_media
            WHERE user_anime_id = ? AND pahe_episode_session = ?
            ORDER BY id DESC LIMIT 1
            """,
            (user_anime_id, pahe_episode_session),
        ).fetchone()
        return self._local_media(row) if row else None

    def add_media(
        self,
        *,
        user_anime_id: int,
        path: str,
        pahe_episode_session: str = "",
        episode: float | None = None,
        label: str = "",
        bytes_size: int = 0,
    ) -> int:
        # Intentional re-download clears a prior delete tombstone
        entry = self.get_user_anime(user_anime_id)
        if entry and entry.mal_id:
            tkey = self.media_tombstone_key(
                entry.mal_id, episode, pahe_episode_session
            )
            if tkey:
                self.clear_tombstone("media", tkey)

        existing = None
        if pahe_episode_session:
            existing = self.get_media_for_episode(user_anime_id, pahe_episode_session)
        if existing:
            self.conn.execute(
                """
                UPDATE local_media
                SET path=?, episode=?, label=?, bytes=?, created_at=?
                WHERE id=?
                """,
                (path, episode, label, bytes_size, _now(), existing.id),
            )
            self.conn.commit()
            return existing.id
        cur = self.conn.execute(
            """
            INSERT INTO local_media
            (media_type, ref_id, user_anime_id, pahe_episode_session, episode, path, label, bytes, created_at)
            VALUES ('anime', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_anime_id,
                user_anime_id,
                pahe_episode_session,
                episode,
                path,
                label,
                bytes_size,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def delete_media(self, media_id: int, *, delete_file: bool = True) -> bool:
        row = self.conn.execute(
            "SELECT path FROM local_media WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            return False
        path = Path(row["path"])
        self.conn.execute("DELETE FROM local_media WHERE id = ?", (media_id,))
        self.conn.commit()
        if delete_file and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        return True

    def get_media_row(self, media_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT m.*, a.mal_id AS mal_id
            FROM local_media m
            LEFT JOIN user_anime ua ON ua.id = m.user_anime_id
            LEFT JOIN anime a ON a.id = ua.anime_id
            WHERE m.id = ?
            """,
            (media_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def add_tombstone(self, entity_type: str, entity_key: str) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO sync_tombstone (entity_type, entity_key, deleted_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_key) DO UPDATE SET
              deleted_at=excluded.deleted_at,
              updated_at=excluded.updated_at
            """,
            (entity_type, entity_key, now, now),
        )
        self.conn.commit()

    def clear_tombstone(self, entity_type: str, entity_key: str) -> None:
        self.conn.execute(
            "DELETE FROM sync_tombstone WHERE entity_type=? AND entity_key=?",
            (entity_type, entity_key),
        )
        self.conn.commit()

    def has_tombstone(self, entity_type: str, entity_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sync_tombstone WHERE entity_type=? AND entity_key=?",
            (entity_type, entity_key),
        ).fetchone()
        return row is not None

    @staticmethod
    def media_tombstone_key(
        mal_id: int | None, episode: float | None, pahe_episode_session: str
    ) -> str | None:
        if not mal_id:
            return None
        return f"{int(mal_id)}:{episode}:{pahe_episode_session or ''}"

    @staticmethod
    def _local_media(r: sqlite3.Row) -> LocalMediaEntry:
        return LocalMediaEntry(
            id=r["id"],
            user_anime_id=r["user_anime_id"],
            pahe_episode_session=r["pahe_episode_session"] or "",
            episode=float(r["episode"]) if r["episode"] is not None else None,
            path=r["path"],
            label=r["label"] or "",
            bytes=int(r["bytes"] or 0),
            created_at=r["created_at"],
        )

    # --- Offline manga chapters ---

    def list_offline_chapters(self, user_manga_id: int) -> list[OfflineChapter]:
        rows = self.conn.execute(
            """
            SELECT * FROM manga_offline_chapter
            WHERE user_manga_id = ?
            ORDER BY id ASC
            """,
            (user_manga_id,),
        ).fetchall()
        return [self._offline_chapter(r) for r in rows]

    def get_offline_chapter(
        self, user_manga_id: int, chapter_id: str
    ) -> OfflineChapter | None:
        row = self.conn.execute(
            """
            SELECT * FROM manga_offline_chapter
            WHERE user_manga_id = ? AND chapter_id = ?
            """,
            (user_manga_id, chapter_id),
        ).fetchone()
        return self._offline_chapter(row) if row else None

    def get_offline_by_chapter_id(self, chapter_id: str) -> OfflineChapter | None:
        row = self.conn.execute(
            """
            SELECT * FROM manga_offline_chapter
            WHERE chapter_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
        return self._offline_chapter(row) if row else None

    def upsert_offline_chapter(
        self,
        *,
        user_manga_id: int,
        chapter_id: str,
        chapter_no: str = "",
        title: str = "",
        pages: int = 0,
        path: str,
        quality: str = "data",
        bytes_size: int = 0,
    ) -> int:
        existing = self.get_offline_chapter(user_manga_id, chapter_id)
        if existing:
            self.conn.execute(
                """
                UPDATE manga_offline_chapter
                SET chapter_no=?, title=?, pages=?, path=?, quality=?, bytes=?, created_at=?
                WHERE id=?
                """,
                (
                    chapter_no,
                    title,
                    pages,
                    path,
                    quality,
                    bytes_size,
                    _now(),
                    existing.id,
                ),
            )
            self.conn.commit()
            return existing.id
        cur = self.conn.execute(
            """
            INSERT INTO manga_offline_chapter
            (user_manga_id, chapter_id, chapter_no, title, pages, path, quality, bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_manga_id,
                chapter_id,
                chapter_no,
                title,
                pages,
                path,
                quality,
                bytes_size,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def delete_offline_chapter(self, user_manga_id: int, chapter_id: str) -> bool:
        cur = self.conn.execute(
            """
            DELETE FROM manga_offline_chapter
            WHERE user_manga_id = ? AND chapter_id = ?
            """,
            (user_manga_id, chapter_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_read_position(self, user_manga_id: int, chapter_id: str) -> int | None:
        row = self.conn.execute(
            """
            SELECT page_index FROM manga_read_position
            WHERE user_manga_id = ? AND chapter_id = ?
            """,
            (user_manga_id, chapter_id),
        ).fetchone()
        return int(row["page_index"]) if row else None

    def list_read_positions(self, user_manga_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT chapter_id, page_index FROM manga_read_position
            WHERE user_manga_id = ?
            """,
            (user_manga_id,),
        ).fetchall()
        return {str(r["chapter_id"]): int(r["page_index"]) for r in rows}

    def set_read_position(
        self, user_manga_id: int, chapter_id: str, page_index: int
    ) -> None:
        page_index = max(0, int(page_index))
        self.conn.execute(
            """
            INSERT INTO manga_read_position (user_manga_id, chapter_id, page_index, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_manga_id, chapter_id) DO UPDATE SET
              page_index=excluded.page_index,
              updated_at=excluded.updated_at
            """,
            (user_manga_id, chapter_id, page_index, _now()),
        )
        self.conn.commit()

    @staticmethod
    def _offline_chapter(r: sqlite3.Row) -> OfflineChapter:
        return OfflineChapter(
            id=r["id"],
            user_manga_id=r["user_manga_id"],
            chapter_id=r["chapter_id"],
            chapter_no=r["chapter_no"] or "",
            title=r["title"] or "",
            pages=int(r["pages"] or 0),
            path=r["path"],
            quality=r["quality"] or "data",
            bytes=int(r["bytes"] or 0),
            created_at=r["created_at"],
        )
