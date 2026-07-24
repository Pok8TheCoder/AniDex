from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from localfun.db.schema import ANIME_STATUSES, MANGA_STATUSES
from localfun.paths import app_data_dir, db_path, output_dir, tokens_path
from localfun.web.deps import repo_ctx
from localfun.web.jobs import JOBS
from localfun.web import security as websec
from localfun.web.security import redact_job_result, redact_path

router = APIRouter(prefix="/api", tags=["api"])


def _anime_dict(e) -> dict[str, Any]:
    return {
        "user_anime_id": e.user_anime_id,
        "anime_id": e.anime_id,
        "mal_id": e.mal_id,
        "title": e.title,
        "title_english": e.title_english,
        "display_title": e.title_english or e.title,
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
        "local_folder": "",  # never expose filesystem paths to the browser

        "updated_at": e.updated_at,
    }


def _manga_cover_src(mangadex_id: str | None, cover_url: str | None = None) -> str | None:
    """Serve covers via our proxy — MangaDex CDN hotlink-blocks localhost."""
    if mangadex_id:
        return f"/api/manga/cover/{mangadex_id}"
    return cover_url


def _manga_dict(e) -> dict[str, Any]:
    return {
        "user_manga_id": e.user_manga_id,
        "manga_id": e.manga_id,
        "mangadex_id": e.mangadex_id,
        "title": e.title,
        "title_english": e.title_english,
        "display_title": e.title_english or e.title,
        "status": e.status,
        "year": e.year,
        "cover_url": _manga_cover_src(e.mangadex_id, e.cover_url),
        "synopsis": e.synopsis,
        "list_status": e.list_status,
        "progress": e.progress,
        "score": e.score,
        "notes": e.notes,
        "local_folder": "",  # never expose filesystem paths to the browser

        "updated_at": e.updated_at,
    }


class ProfilePatch(BaseModel):
    display_name: str | None = None
    mal_client_id: str | None = None
    mal_username: str | None = None


class OnboardBody(BaseModel):
    display_name: str = ""
    mode: str = "empty"  # empty | later


class AnimeUpdate(BaseModel):
    list_status: str | None = None
    progress: int | None = None
    score: int | None = None
    notes: str | None = None
    local_folder: str | None = None


class MangaUpdate(BaseModel):
    list_status: str | None = None
    progress: int | None = None
    score: int | None = None
    notes: str | None = None
    local_folder: str | None = None


class SearchAddAnime(BaseModel):
    mal_id: int
    title: str
    title_english: str | None = None
    media_type: str | None = None
    airing_status: str | None = None
    episodes: int | None = None
    mean_score: float | None = None
    cover_url: str | None = None
    synopsis: str | None = None
    list_status: str = "plan_to_watch"


class SearchAddManga(BaseModel):
    mangadex_id: str
    title: str
    title_english: str | None = None
    status: str | None = None
    year: int | None = None
    cover_url: str | None = None
    synopsis: str | None = None
    list_status: str = "plan_to_read"


class LinkBody(BaseModel):
    query: str | None = None
    chosen_session: str | None = None
    refresh_only: bool = False


class ResolveBody(BaseModel):
    user_anime_id: int
    anime_session: str
    episode_session: str
    episode: float | None = None
    audio: str = "jpn"
    resolution: int = 1080


class DownloadBody(BaseModel):
    user_anime_id: int
    anime_session: str
    episode_session: str
    episode: float | None = None
    audio: str = "jpn"
    resolution: int = 1080


class MangaPdfBody(BaseModel):
    chapter_url: str
    user_manga_id: int | None = None


class OfflineChapterBody(BaseModel):
    chapter_no: str = ""
    title: str = ""
    quality: str = "data-saver"


# ---- profile / meta ----

@router.get("/meta")
def meta(request: Request) -> dict[str, Any]:
    # Never expose full home-directory paths to LAN clients
    if websec.client_is_loopback(request):
        return {
            "app": "LocalFun",
            "data_dir": str(app_data_dir()),
            "db_path": str(db_path()),
            "output_dir": str(output_dir()),
            "anime_statuses": list(ANIME_STATUSES),
            "manga_statuses": list(MANGA_STATUSES),
        }
    return {
        "app": "LocalFun",
        "data_dir": "(local only)",
        "db_path": "(local only)",
        "output_dir": "output",
        "anime_statuses": list(ANIME_STATUSES),
        "manga_statuses": list(MANGA_STATUSES),
    }


@router.get("/profile")
def get_profile(request: Request) -> dict[str, Any]:
    with repo_ctx() as repo:
        p = repo.get_profile()
        # MAL client id is a credential — only return full value on loopback
        cid = p.mal_client_id or ""
        if websec.client_is_loopback(request):
            exposed = cid
        elif cid:
            exposed = ("*" * max(0, len(cid) - 4)) + cid[-4:]
        else:
            exposed = ""
        return {
            "display_name": p.display_name,
            "onboarded": p.onboarded,
            "mal_username": p.mal_username,
            "mal_client_id": exposed,
            "has_mal_client_id": bool(cid),
            "has_mal_tokens": tokens_path().exists(),
        }


@router.patch("/profile")
def patch_profile(request: Request, body: ProfilePatch) -> dict[str, Any]:
    with repo_ctx() as repo:
        # Ignore masked client-id submissions from LAN (all * or ending-only)
        mal_cid = body.mal_client_id
        if mal_cid is not None and not websec.client_is_loopback(request):
            if not mal_cid or set(mal_cid) <= {"*"} or (
                len(mal_cid) > 4 and mal_cid[:-4].replace("*", "") == ""
            ):
                mal_cid = None  # leave existing value unchanged
        repo.update_profile(
            display_name=body.display_name,
            mal_client_id=mal_cid,
            mal_username=body.mal_username,
        )
        return get_profile(request)


@router.post("/onboarding")
def onboard(body: OnboardBody) -> dict[str, Any]:
    with repo_ctx() as repo:
        repo.update_profile(display_name=body.display_name.strip(), onboarded=True)
        return {"ok": True}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    data = job.to_dict()
    data["result"] = redact_job_result(data.get("result"))
    return data


# ---- anime ----

@router.get("/anime")
def list_anime(status: str = "all") -> list[dict[str, Any]]:
    with repo_ctx() as repo:
        entries = repo.list_user_anime(None if status == "all" else status)
        return [_anime_dict(e) for e in entries]


@router.get("/anime/search")
def search_anime(q: str, limit: int = 20) -> list[dict[str, Any]]:
    with repo_ctx() as repo:
        client_id = repo.get_profile().mal_client_id
    if not client_id:
        raise HTTPException(400, "Add a MAL API Client ID in Settings first.")
    from localfun.services.mal_api import MalClient

    client = MalClient(client_id)
    results = client.search_anime(q.strip(), limit=limit)
    return [
        {
            "mal_id": r.mal_id,
            "title": r.title,
            "title_english": r.title_english,
            "media_type": r.media_type,
            "airing_status": r.airing_status,
            "episodes": r.episodes,
            "mean_score": r.mean_score,
            "cover_url": r.cover_url,
            "synopsis": r.synopsis,
        }
        for r in results
    ]


@router.post("/anime/add")
def add_anime(body: SearchAddAnime) -> dict[str, Any]:
    with repo_ctx() as repo:
        anime_id = repo.upsert_anime(
            mal_id=body.mal_id,
            title=body.title,
            title_english=body.title_english,
            media_type=body.media_type,
            airing_status=body.airing_status,
            episodes=body.episodes,
            mean_score=body.mean_score,
            cover_url=body.cover_url,
            synopsis=body.synopsis,
        )
        ua = repo.set_user_anime(anime_id, list_status=body.list_status)
        e = repo.get_user_anime(ua)
        assert e
        return _anime_dict(e)


@router.get("/anime/{user_anime_id}")
def get_anime(user_anime_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        e = repo.get_user_anime(user_anime_id)
        if not e:
            raise HTTPException(404, "Anime not found")
        return _anime_dict(e)


@router.patch("/anime/{user_anime_id}")
def update_anime(user_anime_id: int, body: AnimeUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    with repo_ctx() as repo:
        if not repo.get_user_anime(user_anime_id):
            raise HTTPException(404, "Anime not found")
        if fields:
            repo.update_user_anime_fields(user_anime_id, **fields)
        e = repo.get_user_anime(user_anime_id)
        assert e
        return _anime_dict(e)


@router.delete("/anime/{user_anime_id}")
def delete_anime(user_anime_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        repo.remove_user_anime(user_anime_id)
    return {"ok": True}


# ---- manga ----

@router.get("/manga")
def list_manga(status: str = "all") -> list[dict[str, Any]]:
    with repo_ctx() as repo:
        entries = repo.list_user_manga(None if status == "all" else status)
        return [_manga_dict(e) for e in entries]


@router.get("/manga/search")
def search_manga(q: str, limit: int = 20) -> list[dict[str, Any]]:
    from localfun.services.mangadex import MangaDexClient

    client = MangaDexClient()
    results = client.search(q.strip(), limit=limit)
    return [
        {
            "mangadex_id": r.mangadex_id,
            "title": r.title,
            "title_english": r.title_english,
            "status": r.status,
            "year": r.year,
            "cover_url": _manga_cover_src(r.mangadex_id, r.cover_url),
            "synopsis": r.synopsis,
        }
        for r in results
    ]


@router.get("/manga/cover/{mangadex_id}")
def manga_cover(mangadex_id: str, size: str = "256"):
    """Proxy MangaDex cover art (CDN blocks direct browser loads from localhost)."""
    from fastapi.responses import Response
    from localfun.services.mangadex import MangaDexClient, MangaDexError

    size = size if size in ("256", "512") else "256"
    with repo_ctx() as repo:
        # Prefer stored URL when we already know the filename
        stored = None
        for e in repo.list_user_manga():
            if e.mangadex_id == mangadex_id and e.cover_url:
                stored = e.cover_url
                break

    cover_url = stored
    if cover_url and "/api/" in cover_url:
        cover_url = None
    if not cover_url:
        try:
            meta = MangaDexClient().get_manga(mangadex_id)
            cover_url = meta.cover_url
        except MangaDexError as exc:
            raise HTTPException(502, str(exc)) from exc
    if not cover_url:
        raise HTTPException(404, "No cover")

    # Normalize size suffix (.256.jpg / .512.jpg)
    base = cover_url
    for suffix in (".256.jpg", ".512.jpg"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    url = f"{base}.{size}.jpg"

    try:
        data = MangaDexClient().fetch_cover_bytes(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Cover fetch failed: {exc}") from exc
    ctype = "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ctype = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ctype = "image/webp"
    return Response(
        content=data,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.post("/manga/add")
def add_manga(body: SearchAddManga) -> dict[str, Any]:
    cover_url = body.cover_url
    if not cover_url or cover_url.startswith("/"):
        from localfun.services.mangadex import MangaDexClient, MangaDexError

        try:
            cover_url = MangaDexClient().get_manga(body.mangadex_id).cover_url
        except MangaDexError:
            cover_url = None
    with repo_ctx() as repo:
        manga_id = repo.upsert_manga(
            mangadex_id=body.mangadex_id,
            title=body.title,
            title_english=body.title_english,
            status=body.status,
            year=body.year,
            cover_url=cover_url,
            synopsis=body.synopsis,
        )
        um = repo.set_user_manga(manga_id, list_status=body.list_status)
        e = repo.get_user_manga(um)
        assert e
        return _manga_dict(e)


@router.post("/manga/pdf")
def manga_pdf(body: MangaPdfBody) -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        from pathlib import Path

        from mangadex_to_pdf import export_chapter

        out = output_dir() / "manga"
        if body.user_manga_id:
            with repo_ctx() as repo:
                e = repo.get_user_manga(body.user_manga_id)
                if e and e.local_folder:
                    out = Path(e.local_folder)

        def on_progress(done: int, total: int, msg: str) -> None:
            job.progress = done
            job.total = total
            job.message = msg

        path = export_chapter(
            body.chapter_url,
            out_root=out,
            on_progress=on_progress,
        )
        if body.user_manga_id:
            with repo_ctx() as repo:
                repo.update_user_manga_fields(
                    body.user_manga_id, local_folder=str(path.parent)
                )
        return {"path": redact_path(path)}

    job = JOBS.submit("manga_pdf", work)
    return {"job_id": job.id}


@router.get("/manga/{user_manga_id}/chapters")
def manga_chapters(
    user_manga_id: int,
    lang: str = "en",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    from localfun.services import manga_offline as offline
    from localfun.services.mangadex import MangaDexClient, MangaDexError

    with repo_ctx() as repo:
        e = repo.get_user_manga(user_manga_id)
        if not e:
            raise HTTPException(404, "Manga not found")
        mid = e.mangadex_id
        title = e.title_english or e.title
        saved = {o.chapter_id: o for o in repo.list_offline_chapters(user_manga_id)}
    if not mid:
        raise HTTPException(400, "This entry has no MangaDex id")

    try:
        chapters, total = MangaDexClient().list_chapters(
            mid, lang=lang, limit=limit, offset=offset
        )
    except MangaDexError as exc:
        raise HTTPException(502, str(exc)) from exc

    for ch in chapters:
        cid = ch.get("id") or ""
        off = saved.get(cid)
        ch["offline"] = bool(off)
        ch["offline_pages"] = off.pages if off else 0
        job = offline.active_job(user_manga_id, cid)
        if job and job.status in ("pending", "running"):
            ch["download"] = {
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "total": job.total,
                "message": job.message,
            }
        else:
            ch["download"] = None
    return {
        "user_manga_id": user_manga_id,
        "mangadex_id": mid,
        "title": title,
        "lang": lang,
        "offset": offset,
        "limit": limit,
        "total": total,
        "chapters": chapters,
    }


@router.post("/manga/{user_manga_id}/chapters/{chapter_id}/offline")
def manga_offline_start(
    user_manga_id: int,
    chapter_id: str,
    body: OfflineChapterBody | None = None,
) -> dict[str, Any]:
    from localfun.services import manga_offline as offline
    from mangadex_to_pdf import parse_chapter_id

    body = body or OfflineChapterBody()
    with repo_ctx() as repo:
        if not repo.get_user_manga(user_manga_id):
            raise HTTPException(404, "Manga not found")
        try:
            cid = parse_chapter_id(chapter_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        existing = repo.get_offline_chapter(user_manga_id, cid)
        if existing:
            return {"ok": True, "offline": True, "pages": existing.pages}

    q = "data" if body.quality == "data" else "data-saver"
    job = offline.start_download(
        user_manga_id=user_manga_id,
        chapter_id=cid,
        chapter_no=body.chapter_no,
        title=body.title,
        quality=q,
    )
    return {"ok": True, "job_id": job.id, "offline": False}


@router.get("/manga/{user_manga_id}/chapters/{chapter_id}/adjacent")
def manga_chapter_adjacent(
    user_manga_id: int,
    chapter_id: str,
    lang: str = "en",
) -> dict[str, Any]:
    """Return prev/next chapter ids in the same language feed."""
    from localfun.services.mangadex import MangaDexClient, MangaDexError
    from mangadex_to_pdf import parse_chapter_id

    try:
        cid = parse_chapter_id(chapter_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with repo_ctx() as repo:
        e = repo.get_user_manga(user_manga_id)
        if not e:
            raise HTTPException(404, "Manga not found")
        mid = e.mangadex_id
    if not mid:
        raise HTTPException(400, "This entry has no MangaDex id")

    client = MangaDexClient()
    ids: list[str] = []
    offset = 0
    total = None
    try:
        while True:
            batch, total = client.list_chapters(mid, lang=lang, limit=100, offset=offset)
            if not batch:
                break
            ids.extend(str(ch["id"]) for ch in batch if ch.get("id"))
            offset += len(batch)
            if offset >= (total or 0) or len(batch) < 100:
                break
            if offset > 2000:  # safety
                break
    except MangaDexError as exc:
        raise HTTPException(502, str(exc)) from exc

    try:
        i = ids.index(cid)
    except ValueError:
        return {"chapter_id": cid, "prev": None, "next": None, "index": -1, "total": len(ids)}
    return {
        "chapter_id": cid,
        "prev": ids[i - 1] if i > 0 else None,
        "next": ids[i + 1] if i + 1 < len(ids) else None,
        "index": i,
        "total": len(ids),
    }


@router.delete("/manga/{user_manga_id}/chapters/{chapter_id}/offline")
def manga_offline_delete(user_manga_id: int, chapter_id: str) -> dict[str, Any]:
    from localfun.services import manga_offline as offline
    from mangadex_to_pdf import parse_chapter_id

    with repo_ctx() as repo:
        if not repo.get_user_manga(user_manga_id):
            raise HTTPException(404, "Manga not found")
    try:
        cid = parse_chapter_id(chapter_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    offline.delete_offline(user_manga_id, cid)
    return {"ok": True}


@router.get("/manga/chapter/{chapter_id}/read")
def manga_chapter_read(
    chapter_id: str,
    quality: str = "data-saver",
    user_manga_id: int | None = None,
) -> dict[str, Any]:
    from pathlib import Path

    from localfun.services.manga_offline import list_page_files
    from mangadex_to_pdf import chapter_meta, page_urls, parse_chapter_id

    try:
        cid = parse_chapter_id(chapter_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Prefer offline copy when available
    with repo_ctx() as repo:
        off = (
            repo.get_offline_chapter(user_manga_id, cid)
            if user_manga_id
            else repo.get_offline_by_chapter_id(cid)
        )
    if off and Path(off.path).is_dir():
        files = list_page_files(Path(off.path))
        if files:
            return {
                "chapter_id": cid,
                "manga_title": "",
                "chapter": off.chapter_no,
                "title": off.title,
                "pages_count": len(files),
                "quality": off.quality,
                "offline": True,
                "pages": [
                    f"/api/manga/chapter/{cid}/page/{i}?offline=1" for i in range(len(files))
                ],
            }

    q = "data" if quality == "data" else "data-saver"
    try:
        manga_title, chapter_no, chapter_title, pages = chapter_meta(cid)
        _, urls = page_urls(cid, quality=q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    return {
        "chapter_id": cid,
        "manga_title": manga_title,
        "chapter": chapter_no,
        "title": chapter_title,
        "pages_count": pages or len(urls),
        "quality": q,
        "offline": False,
        "pages": [
            f"/api/manga/chapter/{cid}/page/{i}?quality={q}" for i in range(len(urls))
        ],
    }


@router.get("/manga/chapter/{chapter_id}/page/{index}")
def manga_chapter_page(
    chapter_id: str,
    index: int,
    quality: str = "data-saver",
    offline: int = 0,
):
    from pathlib import Path

    from fastapi.responses import Response
    from localfun.services.manga_offline import list_page_files
    from mangadex_to_pdf import fetch_bytes, page_urls, parse_chapter_id

    try:
        cid = parse_chapter_id(chapter_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if offline:
        with repo_ctx() as repo:
            off = repo.get_offline_by_chapter_id(cid)
        if not off:
            raise HTTPException(404, "Offline chapter not found")
        files = list_page_files(Path(off.path))
        if index < 0 or index >= len(files):
            raise HTTPException(404, "Page out of range")
        data = files[index].read_bytes()
        ctype = "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            ctype = "image/png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ctype = "image/webp"
        return Response(
            content=data,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=604800"},
        )

    q = "data" if quality == "data" else "data-saver"
    try:
        _, urls = page_urls(cid, quality=q)
        if index < 0 or index >= len(urls):
            raise HTTPException(404, "Page out of range")
        data = fetch_bytes(urls[index], chapter_id=cid, quality=q)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    ctype = "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ctype = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ctype = "image/webp"
    return Response(
        content=data,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/manga/{user_manga_id}")
def get_manga(user_manga_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        e = repo.get_user_manga(user_manga_id)
        if not e:
            raise HTTPException(404, "Manga not found")
        return _manga_dict(e)


@router.patch("/manga/{user_manga_id}")
def update_manga(user_manga_id: int, body: MangaUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    with repo_ctx() as repo:
        if not repo.get_user_manga(user_manga_id):
            raise HTTPException(404, "Manga not found")
        if fields:
            repo.update_user_manga_fields(user_manga_id, **fields)
        e = repo.get_user_manga(user_manga_id)
        assert e
        return _manga_dict(e)


@router.delete("/manga/{user_manga_id}")
def delete_manga(user_manga_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        repo.remove_user_manga(user_manga_id)
    return {"ok": True}


# ---- watch / pahe ----

@router.get("/watch/{user_anime_id}/catalog")
def watch_catalog(user_anime_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        e = repo.get_user_anime(user_anime_id)
        if not e:
            raise HTTPException(404, "Anime not found")
        link = repo.get_pahe_link(user_anime_id)
        media = {
            m.pahe_episode_session: {
                "id": m.id,
                "path": redact_path(m.path),
                "label": m.label or redact_path(m.path),
                "episode": m.episode,
                "bytes": m.bytes,
            }
            for m in repo.list_media(user_anime_id)
            if m.pahe_episode_session
        }
        seasons = []
        if link:
            for s in repo.list_pahe_seasons(link.id):
                eps = [
                    {
                        "id": ep.id,
                        "episode": ep.episode,
                        "episode2": ep.episode2,
                        "episode_session": ep.episode_session,
                        "title": ep.title,
                        "duration": ep.duration,
                        "downloaded": ep.episode_session in media,
                        "media_id": media.get(ep.episode_session, {}).get("id"),
                    }
                    for ep in repo.list_pahe_episodes(s.id)
                ]
                seasons.append(
                    {
                        "id": s.id,
                        "pahe_session": s.pahe_session,
                        "label": s.label,
                        "year": s.year,
                        "episode_count": s.episode_count or len(eps),
                        "episodes": eps,
                    }
                )
        return {
            "anime": _anime_dict(e),
            "link": None
            if not link
            else {
                "id": link.id,
                "pahe_session": link.pahe_session,
                "pahe_title": link.pahe_title,
                "poster": link.poster,
                "synced_at": link.synced_at,
            },
            "seasons": seasons,
            "media": list(media.values()),
        }


@router.post("/watch/{user_anime_id}/link")
def watch_link(user_anime_id: int, body: LinkBody) -> dict[str, Any]:
    with repo_ctx() as repo:
        e = repo.get_user_anime(user_anime_id)
        if not e:
            raise HTTPException(404, "Anime not found")
        query = (body.query or e.title_english or e.title).strip()

    def work(job) -> dict[str, Any]:
        from localfun.services.pahe_catalog import link_anime_to_pahe, refresh_pahe_episodes

        job.message = "Working…"
        if body.refresh_only:
            return refresh_pahe_episodes(user_anime_id)
        return link_anime_to_pahe(
            user_anime_id, query, chosen_session=body.chosen_session
        )

    job = JOBS.submit("pahe_link", work)
    return {"job_id": job.id}


@router.get("/watch/pahe-search")
def pahe_search(q: str) -> dict[str, Any]:
    def work(job) -> list[dict[str, Any]]:
        from localfun.services.pahe_catalog import search_anime

        job.message = "Searching AnimePahe…"
        hits = search_anime(q.strip())
        return [
            {
                "session": h.session,
                "title": h.title,
                "type": h.type,
                "year": h.year,
                "episodes": h.episodes,
                "poster": h.poster,
                "status": h.status,
            }
            for h in hits
        ]

    job = JOBS.submit("pahe_search", work)
    return {"job_id": job.id}


@router.post("/watch/resolve")
def watch_resolve(body: ResolveBody) -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        from localfun.services.animepahe import resolve_play_url
        from localfun.services.pahe_catalog import play_url
        from localfun.services.stream_sessions import STREAM_STORE

        job.message = "Resolving stream…"
        url = play_url(body.anime_session, body.episode_session)
        resolved = resolve_play_url(
            url, resolution=body.resolution, audio=body.audio
        )
        job.message = "Caching first seconds…"
        session = STREAM_STORE.create(resolved.m3u8, referer=resolved.referer)
        src = resolved.source
        return {
            "stream_id": session.id,
            "playlist_url": f"/stream/{session.id}/index.m3u8",
            "source_label": src.label if src else "",
            "audio": src.audio if src else body.audio,
            "resolution": src.resolution if src else body.resolution,
            "cached": session.cached_count(),
            "segments": len(session.segments),
            "mode": "progressive_cache",
        }

    job = JOBS.submit("resolve", work)
    return {"job_id": job.id}


@router.post("/watch/download")
def watch_download(body: DownloadBody) -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        from pathlib import Path

        from localfun.services.anime_download import download_episode
        from localfun.services.pahe_catalog import play_url
        from localfun.web.deps import repo_ctx as rc

        job.message = "Starting download…"
        url = play_url(body.anime_session, body.episode_session)
        with rc() as repo:
            e = repo.get_user_anime(body.user_anime_id)
            if not e:
                raise RuntimeError("Anime not found")
            out = Path(e.local_folder) if e.local_folder else output_dir() / "anime"

        def on_log(msg: str) -> None:
            job.message = msg

        def on_progress(done: int, total: int) -> None:
            job.progress = done
            job.total = total

        result = download_episode(
            url,
            out_dir=out,
            resolution=body.resolution,
            audio=body.audio,
            on_log=on_log,
            on_progress=on_progress,
        )
        path = Path(result.path)
        with rc() as repo:
            mid = repo.add_media(
                user_anime_id=body.user_anime_id,
                path=str(path),
                pahe_episode_session=body.episode_session,
                episode=body.episode,
                label=path.name,
                bytes_size=path.stat().st_size if path.is_file() else 0,
            )
            entry = repo.get_user_anime(body.user_anime_id)
            if entry and not entry.local_folder:
                repo.update_user_anime_fields(
                    body.user_anime_id, local_folder=str(path.parent)
                )
        return {"media_id": mid, "path": redact_path(path)}

    job = JOBS.submit("download", work)
    return {"job_id": job.id}


@router.get("/media/{media_id}/file")
def media_file(media_id: int):
    from pathlib import Path

    from fastapi.responses import FileResponse

    with repo_ctx() as repo:
        row = repo.conn.execute(
            "SELECT path FROM local_media WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Media not found")
        path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.delete("/media/{media_id}")
def delete_media(media_id: int) -> dict[str, Any]:
    with repo_ctx() as repo:
        ok = repo.delete_media(media_id, delete_file=True)
        if not ok:
            raise HTTPException(404, "Media not found")
    return {"ok": True}


# ---- settings / sync / cf ----

@router.post("/settings/cloudflare")
def start_cloudflare() -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        from localfun.services.pahe_browser import wait_for_cloudflare

        job.message = "Waiting for Cloudflare in Chrome…"
        ok = wait_for_cloudflare()
        if not ok:
            raise RuntimeError("Cloudflare not cleared in time.")
        return {"ok": True}

    job = JOBS.submit("cloudflare", work)
    return {"job_id": job.id}


@router.get("/settings/cloudflare/status")
def cf_status() -> dict[str, Any]:
    try:
        from localfun.services.pahe_browser import cloudflare_cleared

        return {"cleared": cloudflare_cleared()}
    except Exception as exc:  # noqa: BLE001
        return {"cleared": False, "error": str(exc)}


@router.post("/settings/mal/oauth")
def mal_oauth() -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        with repo_ctx() as repo:
            client_id = repo.get_profile().mal_client_id
        if not client_id:
            raise RuntimeError("Set MAL Client ID first.")
        from localfun.services.mal_api import MalClient

        job.message = "Waiting for MAL OAuth in browser…"
        client = MalClient(client_id)
        name = client.login_oauth_pkce()
        with repo_ctx() as repo:
            repo.update_profile(mal_username=name)
        return {"mal_username": name}

    job = JOBS.submit("mal_oauth", work)
    return {"job_id": job.id}


@router.post("/settings/mal/sync")
def mal_sync(mode: str = "pull") -> dict[str, Any]:
    def work(job) -> dict[str, Any]:
        from localfun.services.mal_api import MalClient
        from localfun.services.sync import pull_mal_list, push_local_to_mal

        with repo_ctx() as repo:
            client_id = repo.get_profile().mal_client_id
        if not client_id:
            raise RuntimeError("Set MAL Client ID first.")
        client = MalClient(client_id)
        if not client.is_authenticated:
            raise RuntimeError("Connect MAL with OAuth first.")
        with repo_ctx() as repo:
            if mode == "push":
                job.message = "Pushing to MAL…"
                n = push_local_to_mal(repo, client)
                return {"updated": n[0], "failed": n[1], "mode": mode}
            job.message = "Pulling from MAL…"
            count = pull_mal_list(repo, client)
            return {"count": count, "mode": mode}

    job = JOBS.submit("mal_sync", work)
    return {"job_id": job.id}


@router.post("/settings/mal/import-xml")
async def import_xml(file: UploadFile = File(...)) -> dict[str, Any]:
    from localfun.services.sync import import_xml_into_repo

    data = await file.read()
    tmp = app_data_dir() / "_import.xml"
    tmp.write_bytes(data)
    with repo_ctx() as repo:
        n = import_xml_into_repo(repo, str(tmp))
    return {"imported": n[0], "updated": n[1]}


@router.get("/settings/mal/export-xml")
def export_xml():
    from fastapi.responses import FileResponse
    from localfun.services.sync import export_repo_to_xml

    out = app_data_dir() / "localfun_export.xml"
    with repo_ctx() as repo:
        export_repo_to_xml(repo, out)
    return FileResponse(out, filename="localfun_export.xml", media_type="application/xml")
