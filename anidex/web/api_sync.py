"""Peer sync HTTP API + live WebSocket."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from anidex.db.schema import open_repo
from anidex.sync import APP_NAME, PROTOCOL_VERSION
from anidex.sync import live as live_sync
from anidex.sync.capabilities import hello_payload, local_capabilities, playwright_available
from anidex.sync.client import run_sync
from anidex.sync.manifest import apply_batches, build_manifest, export_entities
from anidex.sync.media import (
    find_media_id_for_key,
    pack_offline_chapter,
    receive_anime_file,
    receive_offline_zip,
    resolve_anime_media_path,
)
from anidex.sync.token import (
    ensure_sync_identity,
    get_sync_identity,
    recent_sync_logs,
    rotate_sync_token,
    set_peer_url,
    set_sync_token,
    sync_token_ok,
)
from anidex.web.jobs import JOBS

router = APIRouter(prefix="/api/sync", tags=["sync"])


def _extract_token(
    request: Request,
    authorization: str | None,
    x_token: str | None,
) -> str | None:
    if x_token:
        return x_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.query_params.get("token")


def require_sync_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> None:
    tok = _extract_token(request, authorization, x_anidex_sync_token)
    if not sync_token_ok(tok):
        raise HTTPException(401, "Invalid or missing sync token")


class PullBody(BaseModel):
    keys: list[str] = Field(default_factory=list)


class PushBody(BaseModel):
    batches: dict[str, Any] = Field(default_factory=dict)


class RunBody(BaseModel):
    peer_url: str | None = None


class RemoteDownloadBody(BaseModel):
    anime_session: str
    episode_session: str
    episode: float | None = None
    audio: str = "jpn"
    resolution: int = 1080
    mal_id: int | None = None
    title: str = ""
    user_anime_id: int | None = None


class RemotePaheLinkBody(BaseModel):
    mal_id: int | None = None
    title: str = ""
    query: str = ""
    chosen_session: str | None = None
    refresh_only: bool = False


@router.get("/hello")
def sync_hello(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    ident = ensure_sync_identity()
    return hello_payload(ident["device_id"], app=APP_NAME, protocol=PROTOCOL_VERSION)


@router.websocket("/ws")
async def sync_ws(websocket: WebSocket) -> None:
    """Persistent peer channel for auto-sync signals."""
    token = (
        websocket.query_params.get("token")
        or websocket.headers.get("x-anidex-sync-token")
        or websocket.headers.get("authorization", "")
    )
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not sync_token_ok(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        await live_sync.handle_inbound(websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/manifest")
def sync_manifest(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    return build_manifest()


@router.post("/pull")
def sync_pull(
    body: PullBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    keys = body.keys[:5000]
    return {"batches": export_entities(keys)}


@router.post("/push")
def sync_push(
    body: PushBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    counts = apply_batches(body.batches or {})
    return {"ok": True, "counts": counts}


@router.get("/media/anime/{key:path}")
def sync_media_anime(
    key: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
):
    require_sync_token(request, authorization, x_anidex_sync_token)
    if not key.startswith("media:"):
        key = "media:" + key
    mid = find_media_id_for_key(key)
    if not mid:
        raise HTTPException(404, "Media not found")
    path = resolve_anime_media_path(mid)
    if not path:
        raise HTTPException(404, "Media file missing on disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/media/offline/{mangadex_id}/{chapter_id}")
def sync_media_offline(
    mangadex_id: str,
    chapter_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
):
    require_sync_token(request, authorization, x_anidex_sync_token)
    conn, repo = open_repo()
    try:
        entry = None
        for e in repo.list_user_manga():
            if e.mangadex_id == mangadex_id:
                entry = e
                break
        if not entry:
            raise HTTPException(404, "Manga not on this device")
        data = pack_offline_chapter(entry.user_manga_id, chapter_id)
        if not data:
            raise HTTPException(404, "Offline chapter not found")
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{chapter_id}.zip"'
            },
        )
    finally:
        conn.close()


@router.put("/media/anime")
async def sync_media_anime_put(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
    x_anidex_mal_id: str | None = Header(default=None, alias="X-AniDex-Mal-Id"),
    x_anidex_episode: str | None = Header(default=None, alias="X-AniDex-Episode"),
    x_anidex_pahe_session: str | None = Header(
        default=None, alias="X-AniDex-Pahe-Session"
    ),
    x_anidex_label: str | None = Header(default=None, alias="X-AniDex-Label"),
    x_anidex_filename: str | None = Header(default=None, alias="X-AniDex-Filename"),
    x_anidex_title: str | None = Header(default=None, alias="X-AniDex-Title"),
):
    require_sync_token(request, authorization, x_anidex_sync_token)
    if not x_anidex_mal_id:
        raise HTTPException(400, "Missing X-AniDex-Mal-Id")
    try:
        mal_id = int(x_anidex_mal_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid mal_id") from e
    episode = None
    if x_anidex_episode not in (None, "", "None", "null"):
        try:
            episode = float(x_anidex_episode)  # type: ignore[arg-type]
        except ValueError:
            episode = None
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty body")
    mid = receive_anime_file(
        mal_id=mal_id,
        episode=episode,
        pahe_episode_session=x_anidex_pahe_session or "",
        label=x_anidex_label or "",
        filename=x_anidex_filename or f"{mal_id}.mp4",
        data=data,
        title=x_anidex_title or "Anime",
    )
    return {"ok": True, "local_media_id": mid}


@router.put("/media/offline/{mangadex_id}/{chapter_id}")
async def sync_media_offline_put(
    mangadex_id: str,
    chapter_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
    x_anidex_chapter_no: str | None = Header(default=None, alias="X-AniDex-Chapter-No"),
    x_anidex_title: str | None = Header(default=None, alias="X-AniDex-Title"),
    x_anidex_quality: str | None = Header(default=None, alias="X-AniDex-Quality"),
):
    require_sync_token(request, authorization, x_anidex_sync_token)
    data = await request.body()
    if not data:
        raise HTTPException(400, "Empty body")
    receive_offline_zip(
        mangadex_id=mangadex_id,
        chapter_id=chapter_id,
        chapter_no=x_anidex_chapter_no or "",
        title=x_anidex_title or "",
        quality=x_anidex_quality or "data",
        data=data,
    )
    return {"ok": True}


def _run_remote_download_job(body: RemoteDownloadBody):
    from pathlib import Path

    from anidex.paths import output_dir
    from anidex.services.anime_download import download_episode
    from anidex.services.pahe_catalog import play_url
    from anidex.web.deps import repo_ctx
    from anidex.web.security import redact_path

    if not playwright_available():
        raise HTTPException(
            503,
            "This peer cannot download AnimePahe (Playwright missing).",
        )

    def work(job) -> dict[str, Any]:
        job.message = "Remote download starting…"
        url = play_url(body.anime_session, body.episode_session)
        user_anime_id = body.user_anime_id
        with repo_ctx() as repo:
            e = None
            if user_anime_id:
                e = repo.get_user_anime(user_anime_id)
            if e is None and body.mal_id:
                for row in repo.list_user_anime():
                    if row.mal_id == body.mal_id:
                        e = row
                        user_anime_id = row.user_anime_id
                        break
            if e is None and body.mal_id:
                anime_id = repo.upsert_anime(
                    mal_id=body.mal_id,
                    title=body.title or f"MAL {body.mal_id}",
                )
                user_anime_id = repo.set_user_anime(anime_id, list_status="watching")
                e = repo.get_user_anime(user_anime_id)
            if not e or not user_anime_id:
                raise RuntimeError("Anime not found for remote download")
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
        with repo_ctx() as repo:
            mid = repo.add_media(
                user_anime_id=user_anime_id,
                path=str(path),
                pahe_episode_session=body.episode_session,
                episode=body.episode,
                label=path.name,
                bytes_size=path.stat().st_size if path.is_file() else 0,
            )
            entry = repo.get_user_anime(user_anime_id)
            if entry and not entry.local_folder:
                repo.update_user_anime_fields(
                    user_anime_id, local_folder=str(path.parent)
                )
            mal_id = entry.mal_id if entry else body.mal_id

        live_sync.notify_download_done(
            mal_id=mal_id,
            episode=body.episode,
            key=f"media:{mal_id}:{body.episode}:{body.episode_session}",
        )
        return {
            "media_id": mid,
            "path": redact_path(path),
            "mal_id": mal_id,
            "episode": body.episode,
        }

    return JOBS.submit("remote_download", work)


@router.post("/remote-download")
def sync_remote_download(
    body: RemoteDownloadBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    job = _run_remote_download_job(body)
    return {"job_id": job.id, "ok": True}


@router.get("/remote-download/{job_id}")
def sync_remote_download_status(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@router.post("/remote-pahe-link")
def sync_remote_pahe_link(
    body: RemotePaheLinkBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    """PC-only: search/link/refresh AnimePahe and return catalog for the phone."""
    require_sync_token(request, authorization, x_anidex_sync_token)
    if not playwright_available():
        raise HTTPException(503, "This peer cannot talk to AnimePahe (Playwright missing).")

    from anidex.services.pahe_catalog import link_anime_to_pahe, refresh_pahe_episodes
    from anidex.sync.pahe_catalog_sync import export_pahe_for_entry
    from anidex.web.deps import repo_ctx

    query = (body.query or body.title or "").strip()
    user_anime_id = None
    with repo_ctx() as repo:
        entry = None
        if body.mal_id:
            for e in repo.list_user_anime():
                if e.mal_id == body.mal_id:
                    entry = e
                    break
        if entry is None and body.mal_id:
            anime_id = repo.upsert_anime(
                mal_id=body.mal_id,
                title=body.title or query or f"MAL {body.mal_id}",
            )
            user_anime_id = repo.set_user_anime(anime_id, list_status="watching")
            entry = repo.get_user_anime(user_anime_id)
        if not entry:
            raise HTTPException(404, "Anime not found on PC for Pahe link")
        user_anime_id = entry.user_anime_id
        if not query:
            query = entry.title_english or entry.title
        mal_id = entry.mal_id

    if body.refresh_only:
        result = refresh_pahe_episodes(user_anime_id)
    else:
        result = link_anime_to_pahe(
            user_anime_id, query, chosen_session=body.chosen_session
        )

    with repo_ctx() as repo:
        catalog = export_pahe_for_entry(
            repo, mal_id=int(mal_id), user_anime_id=user_anime_id
        )
    live_sync.mark_dirty(reason="remote_pahe_link")
    return {"ok": True, "result": result, "catalog": catalog}


@router.get("/remote-pahe-search")
def sync_remote_pahe_search(
    request: Request,
    q: str = "",
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> list[dict[str, Any]]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    if not playwright_available():
        raise HTTPException(503, "This peer cannot talk to AnimePahe (Playwright missing).")
    from anidex.services.pahe_catalog import search_anime

    hits = search_anime((q or "").strip())
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


@router.get("/status")
def sync_status(request: Request) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request) and not websec.session_ok(request):
        raise HTTPException(401, "Login required")
    ident = get_sync_identity()
    return {
        "device_id": ident["device_id"],
        "sync_token": ident["sync_token"],
        "peer_url": ident["peer_url"],
        "logs": recent_sync_logs(15),
        "live": live_sync.status(),
        "capabilities": local_capabilities(),
    }


class ConfigBody(BaseModel):
    peer_url: str | None = None
    sync_token: str | None = None


@router.post("/settings")
def sync_settings(request: Request, body: ConfigBody) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request) and not websec.session_ok(request):
        raise HTTPException(401, "Login required")
    if body.peer_url is not None:
        set_peer_url(body.peer_url)
    if body.sync_token:
        set_sync_token(body.sync_token)
    live_info = live_sync.connect_now()
    return {"ok": True, **get_sync_identity(), "live": live_info}


@router.post("/rotate-token")
def sync_rotate(request: Request) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request):
        raise HTTPException(403, "Rotate token only from this PC")
    token = rotate_sync_token()
    live_sync.kick()
    return {"ok": True, "sync_token": token, **get_sync_identity()}


@router.post("/run")
def sync_run(request: Request, body: RunBody | None = None) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request) and not websec.session_ok(request):
        raise HTTPException(401, "Login required")
    body = body or RunBody()
    # Persist peer URL from the form, then open/refresh live WebSocket
    if body.peer_url:
        set_peer_url(body.peer_url.strip())
    live_info = live_sync.connect_now()
    try:
        result = run_sync(body.peer_url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    result = dict(result or {})
    result["live"] = live_sync.status() or live_info
    return result
