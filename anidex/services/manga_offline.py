"""Offline manga chapter downloads (page images on disk)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from anidex.db import open_repo
from anidex.paths import manga_offline_dir
from anidex.web.jobs import JOBS, Job

# (user_manga_id, chapter_id) -> job_id
ACTIVE: dict[tuple[int, str], str] = {}
_CANCEL: set[tuple[int, str]] = set()
_LOCK = threading.Lock()


def active_job(user_manga_id: int, chapter_id: str) -> Job | None:
    with _LOCK:
        jid = ACTIVE.get((user_manga_id, chapter_id))
    if not jid:
        return None
    job = JOBS.get(jid)
    if not job or job.status in ("done", "error"):
        with _LOCK:
            ACTIVE.pop((user_manga_id, chapter_id), None)
        return job if job and job.status == "running" else None
    return job


def request_cancel(user_manga_id: int, chapter_id: str) -> None:
    key = (user_manga_id, chapter_id)
    with _LOCK:
        _CANCEL.add(key)


def _cancelled(user_manga_id: int, chapter_id: str) -> bool:
    with _LOCK:
        return (user_manga_id, chapter_id) in _CANCEL


def _clear_cancel(user_manga_id: int, chapter_id: str) -> None:
    with _LOCK:
        _CANCEL.discard((user_manga_id, chapter_id))
        ACTIVE.pop((user_manga_id, chapter_id), None)


def list_page_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ]
    return sorted(files, key=lambda p: p.name)


def start_download(
    *,
    user_manga_id: int,
    chapter_id: str,
    chapter_no: str = "",
    title: str = "",
    quality: str = "data",
) -> Job:
    key = (user_manga_id, chapter_id)
    existing = active_job(user_manga_id, chapter_id)
    if existing:
        return existing

    _clear_cancel(user_manga_id, chapter_id)

    def work(job: Job) -> dict:
        from mangadex_to_pdf import chapter_meta, download_pages, parse_chapter_id

        cid = parse_chapter_id(chapter_id)
        out = manga_offline_dir(user_manga_id, cid)
        # Fresh folder
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)

        try:
            manga_title, ch_no, ch_title, _pages = chapter_meta(cid)
            job.message = f"Ch.{ch_no}"
            label_no = chapter_no or ch_no
            label_title = title or ch_title

            def on_progress(done: int, total: int, _name: str) -> None:
                if _cancelled(user_manga_id, cid):
                    raise RuntimeError("Download cancelled")
                job.progress = done
                job.total = total
                job.message = f"Ch.{label_no} · {done}/{total}"
                job.updated_at = __import__("time").time()

            images = download_pages(
                cid,
                out,
                quality=quality,
                delay=0.15,
                on_progress=on_progress,
            )
            if _cancelled(user_manga_id, cid):
                raise RuntimeError("Download cancelled")
            if not images:
                raise RuntimeError("No pages downloaded")

            total_bytes = sum(p.stat().st_size for p in images if p.is_file())
            conn, repo = open_repo()
            try:
                repo.upsert_offline_chapter(
                    user_manga_id=user_manga_id,
                    chapter_id=cid,
                    chapter_no=str(label_no),
                    title=label_title or "",
                    pages=len(images),
                    path=str(out),
                    quality=quality,
                    bytes_size=total_bytes,
                )
            finally:
                conn.close()

            job.message = f"Ch.{label_no} · saved {len(images)}p"
            return {
                "chapter_id": cid,
                "pages": len(images),
                "path": str(out),
                "manga_title": manga_title,
            }
        except Exception:
            shutil.rmtree(out, ignore_errors=True)
            raise
        finally:
            _clear_cancel(user_manga_id, cid)

    job = JOBS.submit("manga_offline", work)
    with _LOCK:
        ACTIVE[key] = job.id
    return job


def delete_offline(user_manga_id: int, chapter_id: str) -> bool:
    """Cancel in-flight download and/or remove saved chapter."""
    request_cancel(user_manga_id, chapter_id)
    conn, repo = open_repo()
    try:
        entry = repo.get_offline_chapter(user_manga_id, chapter_id)
        if entry:
            folder = Path(entry.path)
            repo.delete_offline_chapter(user_manga_id, chapter_id)
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
            return True
    finally:
        conn.close()
    # Also wipe partial download folder
    folder = manga_offline_dir(user_manga_id, chapter_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
        return True
    return False
