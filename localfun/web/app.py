from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from localfun.db import init_db
from localfun.web.api import router as api_router
from localfun.web.jobs import JOBS
from localfun.web.security import LocalAccessMiddleware, sync_lan_from_env
from localfun.services.stream_sessions import STREAM_STORE

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sync_lan_from_env()
    init_db()
    from localfun.web.auth import ensure_auth_defaults

    ensure_auth_defaults()
    yield
    try:
        from localfun.services.pahe_browser import close_browser

        close_browser()
    except Exception:
        pass
    STREAM_STORE.clear()
    JOBS.shutdown()


# OpenAPI/docs disabled — this is a local app; avoid advertising every route on the LAN
app = FastAPI(
    title="LocalFun",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(LocalAccessMiddleware)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/stream/{sid}/index.m3u8")
def stream_playlist(sid: str) -> Response:
    session = STREAM_STORE.get(sid)
    if not session:
        raise HTTPException(404, "Stream session expired")
    return Response(
        content=session.playlist_body,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/stream/{sid}/seg/{idx}.ts")
def stream_segment(sid: str, idx: int) -> Response:
    session = STREAM_STORE.get(sid)
    if not session:
        raise HTTPException(404, "Stream session expired")
    try:
        data = session.fetch_segment(idx)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    session.schedule_prefetch(session.playhead)
    return Response(
        content=data,
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store"},
    )


class PlayheadBody(BaseModel):
    seconds: float = Field(ge=0)


@app.post("/stream/{sid}/playhead")
def stream_playhead(sid: str, body: PlayheadBody) -> dict:
    session = STREAM_STORE.get(sid)
    if not session:
        raise HTTPException(404, "Stream session expired")
    session.set_playhead(body.seconds)
    return {
        "ok": True,
        "cached": session.cached_count(),
        "total": len(session.segments),
        "playhead": session.playhead,
    }


@app.delete("/stream/{sid}")
def drop_stream(sid: str) -> dict:
    STREAM_STORE.drop(sid)
    return {"ok": True}


def _page(name: str, request: Request, **ctx):
    return TEMPLATES.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    from localfun.web.deps import repo_ctx

    with repo_ctx() as repo:
        onboarded = repo.get_profile().onboarded
    if not onboarded:
        return _page("onboarding.html", request)
    return _page("anime.html", request, active="anime")


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request):
    return _page("onboarding.html", request)


@app.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request):
    # Discover lives inside Anime as a chip
    return _page("anime.html", request, active="anime")


@app.get("/anime", response_class=HTMLResponse)
def anime_page(request: Request):
    return _page("anime.html", request, active="anime")


@app.get("/upcoming", response_class=HTMLResponse)
def upcoming_page(request: Request):
    return _page("upcoming.html", request, active="anime")


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request):
    return _page("downloads.html", request, active="downloads")


@app.get("/manga/upcoming", response_class=HTMLResponse)
def manga_upcoming_page(request: Request):
    return _page("manga_upcoming.html", request, active="manga")


@app.get("/watch/{user_anime_id}", response_class=HTMLResponse)
def watch_page(request: Request, user_anime_id: int):
    return _page(
        "watch.html", request, active="anime", user_anime_id=user_anime_id
    )


@app.get("/manga", response_class=HTMLResponse)
def manga_page(request: Request):
    return _page("manga.html", request, active="manga")


@app.get("/manga/read/{chapter_id}", response_class=HTMLResponse)
def manga_reader_page(request: Request, chapter_id: str):
    return _page(
        "manga_reader.html",
        request,
        active="manga",
        chapter_id=chapter_id,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _page("settings.html", request, active="settings")
