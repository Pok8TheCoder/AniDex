"""Peer sync HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from anidex.db.schema import open_repo
from anidex.sync import APP_NAME, PROTOCOL_VERSION
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
    # also accept query for simple tools
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


@router.get("/hello")
def sync_hello(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anidex_sync_token: str | None = Header(default=None, alias="X-AniDex-Sync-Token"),
) -> dict[str, Any]:
    require_sync_token(request, authorization, x_anidex_sync_token)
    ident = ensure_sync_identity()
    return {
        "app": APP_NAME,
        "protocol": PROTOCOL_VERSION,
        "device_id": ident["device_id"],
        "capabilities": ["library", "anime_media", "manga_offline", "positions"],
    }


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
    """Receive anime episode bytes from a peer."""
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


@router.get("/status")
def sync_status(request: Request) -> dict[str, Any]:
    """Local status — loopback or logged-in session handled by middleware."""
    from anidex.web import security as websec

    if not websec.client_is_loopback(request) and not websec.session_ok(request):
        raise HTTPException(401, "Login required")
    ident = get_sync_identity()
    return {
        "device_id": ident["device_id"],
        "sync_token": ident["sync_token"],
        "peer_url": ident["peer_url"],
        "logs": recent_sync_logs(15),
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
    return {"ok": True, **get_sync_identity()}


@router.post("/rotate-token")
def sync_rotate(request: Request) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request):
        raise HTTPException(403, "Rotate token only from this PC")
    token = rotate_sync_token()
    return {"ok": True, "sync_token": token, **get_sync_identity()}


@router.post("/run")
def sync_run(request: Request, body: RunBody | None = None) -> dict[str, Any]:
    from anidex.web import security as websec

    if not websec.client_is_loopback(request) and not websec.session_ok(request):
        raise HTTPException(401, "Login required")
    body = body or RunBody()
    try:
        return run_sync(body.peer_url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
