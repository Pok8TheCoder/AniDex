"""Media transfer helpers for peer sync."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx

from anidex.db.schema import open_repo
from anidex.paths import manga_offline_dir, output_dir


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_anime_media_path(media_id: int) -> Path | None:
    conn, repo = open_repo()
    try:
        row = repo.conn.execute(
            "SELECT path FROM local_media WHERE id=?", (media_id,)
        ).fetchone()
        if not row:
            return None
        path = Path(row["path"])
        return path if path.is_file() else None
    finally:
        conn.close()


def find_media_id_for_key(key: str) -> int | None:
    """Parse media:mal:ep:sess and find local_media id."""
    if not key.startswith("media:"):
        return None
    parts = key.split(":")
    try:
        mal_id = int(parts[1])
    except (IndexError, ValueError):
        return None
    ep = parts[2] if len(parts) > 2 else ""
    sess = parts[3] if len(parts) > 3 else ""
    conn, repo = open_repo()
    try:
        for e in repo.list_user_anime():
            if e.mal_id != mal_id:
                continue
            for m in repo.list_media(e.user_anime_id):
                if sess and m.pahe_episode_session != sess:
                    continue
                if ep not in ("", "None", "null") and str(m.episode) != str(ep):
                    # try float compare
                    try:
                        if float(m.episode) != float(ep):
                            continue
                    except (TypeError, ValueError):
                        continue
                return m.id
        return None
    finally:
        conn.close()


def receive_anime_file(
    *,
    mal_id: int,
    episode: float | None,
    pahe_episode_session: str,
    label: str,
    filename: str,
    data: bytes,
    title: str = "Anime",
) -> int:
    """Write anime bytes to disk and register local_media. Returns media id."""
    conn, repo = open_repo()
    try:
        anime_id = None
        user_anime_id = None
        for e in repo.list_user_anime():
            if e.mal_id == mal_id:
                anime_id = e.anime_id
                user_anime_id = e.user_anime_id
                break
        if user_anime_id is None:
            anime_id = repo.upsert_anime(mal_id=mal_id, title=title or f"MAL {mal_id}")
            user_anime_id = repo.set_user_anime(anime_id, list_status="watching")

        dest_dir = output_dir() / "anime"
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = filename or f"{mal_id}_ep{episode or 0}.mp4"
        dest = dest_dir / safe_name
        # avoid clobber different content
        if dest.exists():
            existing_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
            if existing_hash == _sha256_bytes(data):
                mid = repo.add_media(
                    user_anime_id=user_anime_id,
                    path=str(dest),
                    pahe_episode_session=pahe_episode_session or "",
                    episode=episode,
                    label=label or safe_name,
                    bytes_size=len(data),
                )
                return mid
            dest = dest_dir / f"{dest.stem}_sync{dest.suffix}"
        dest.write_bytes(data)
        return repo.add_media(
            user_anime_id=user_anime_id,
            path=str(dest),
            pahe_episode_session=pahe_episode_session or "",
            episode=episode,
            label=label or safe_name,
            bytes_size=len(data),
        )
    finally:
        conn.close()


def pack_offline_chapter(user_manga_id: int, chapter_id: str) -> bytes | None:
    folder = manga_offline_dir(user_manga_id, chapter_id)
    if not folder.is_dir():
        return None
    files = sorted(
        [
            p
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        ],
        key=lambda p: p.name,
    )
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    return buf.getvalue()


def receive_offline_zip(
    *,
    mangadex_id: str,
    chapter_id: str,
    chapter_no: str,
    title: str,
    quality: str,
    data: bytes,
) -> None:
    conn, repo = open_repo()
    try:
        entry = None
        for e in repo.list_user_manga():
            if e.mangadex_id == mangadex_id:
                entry = e
                break
        if not entry:
            manga_id = repo.upsert_manga(
                mangadex_id=mangadex_id, title=title or mangadex_id
            )
            um = repo.set_user_manga(manga_id, list_status="reading")
            entry = repo.get_user_manga(um)
        assert entry
        folder = manga_offline_dir(entry.user_manga_id, chapter_id)
        # clear old pages
        for p in folder.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            zf.extractall(folder)
        pages = len(
            [
                p
                for p in folder.iterdir()
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            ]
        )
        repo.upsert_offline_chapter(
            user_manga_id=entry.user_manga_id,
            chapter_id=chapter_id,
            chapter_no=chapter_no or "",
            title=title or "",
            pages=pages,
            path=str(folder),
            quality=quality or "data-saver",
            bytes_size=len(data),
        )
    finally:
        conn.close()


def download_url(url: str, *, headers: dict[str, str], timeout: float = 600.0) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        return r.content
