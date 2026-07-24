"""Browser session for AnimePahe (Cloudflare + page loads)."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from anidex.paths import app_data_dir

PROFILE_DIR = app_data_dir() / "pahe_browser_profile"
PAHE_HOME = "https://animepahe.pw/"

T = TypeVar("T")

_worker: "_BrowserWorker | None" = None
_worker_lock = threading.Lock()


def profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


class _BrowserWorker(threading.Thread):
    """Owns Playwright on one thread (required by Playwright sync API)."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="AniDex-PaheBrowser")
        self._jobs: queue.Queue[tuple[Callable[[], Any], queue.Queue[Any]] | None] = (
            queue.Queue()
        )
        self._playwright = None
        self._ctx = None
        self._page = None
        self._playwright_missing = False
        self._ready = threading.Event()

    def run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._playwright_missing = True
            self._ready.set()
            return
        self._playwright_missing = False
        self._playwright = sync_playwright().start()
        self._ctx = self._playwright.chromium.launch_persistent_context(
            str(profile_dir()),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        try:
            self._page = self._prepare_work_page()
        except Exception:
            self._page = None
        finally:
            self._ready.set()
        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, result_q = job
            try:
                result_q.put(("ok", fn()))
            except Exception as e:  # noqa: BLE001
                result_q.put(("err", e))
            finally:
                self._jobs.task_done()

        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None
        self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _prepare_work_page(self):
        assert self._ctx is not None
        pages = list(self._ctx.pages)
        if pages:
            pg = pages[0]
            for extra in pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
        else:
            pg = self._ctx.new_page()
        pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=120_000)
        pg.wait_for_timeout(1000)
        return pg

    def submit(self, fn: Callable[[], T], timeout: float = 300.0) -> T:
        if not self._ready.wait(timeout=30):
            raise RuntimeError("Browser worker failed to start.")
        if getattr(self, "_playwright_missing", False):
            raise RuntimeError(
                "AnimePahe needs Playwright (PC only). On Termux, sync episodes "
                "from the PC instead: Settings → Peer sync. On PC: "
                "pip install -r requirements-pahe.txt && playwright install chromium"
            )
        if self._ctx is None or self._page is None:
            raise RuntimeError(
                "Chrome failed to start for AnimePahe. Close any stuck Chrome "
                "windows from AniDex, then use Settings -> Pass AnimePahe "
                "Cloudflare again."
            )
        result_q: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._jobs.put((fn, result_q))
        kind, payload = result_q.get(timeout=timeout)
        if kind == "err":
            raise payload
        return payload

    def stop(self) -> None:
        self._jobs.put(None)
        self.join(timeout=15)


def _worker_instance() -> _BrowserWorker:
    global _worker
    with _worker_lock:
        dead = _worker is None or not _worker.is_alive()
        failed = (
            _worker is not None
            and _worker.is_alive()
            and _worker._ready.is_set()
            and _worker._page is None
        )
        if dead or failed:
            if _worker is not None and _worker.is_alive():
                _worker.stop()
            _worker = _BrowserWorker()
            _worker.start()
        return _worker


def _page():
    worker = _worker_instance()
    assert worker._page is not None
    pg = worker._page
    if not pg.url or pg.url == "about:blank":
        pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=120_000)
    return pg


def _is_cf(title: str, html: str) -> bool:
    t = (title or "").lower()
    h = (html or "")[:3000].lower()
    return (
        "just a moment" in t
        or "attention" in t
        or "just a moment" in h
        or "cf-browser-verification" in h
    )


def _ensure_pahe_session(pg) -> None:
    if "animepahe." not in (pg.url or ""):
        pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=120_000)
        pg.wait_for_timeout(1500)
    if _is_cf(pg.title(), pg.content()):
        raise RuntimeError(
            "Cloudflare not passed yet. Open Settings -> Pass AnimePahe Cloudflare, "
            "wait until animepahe.pw loads, then try again."
        )


def _wait_for_html(pg, *, kwik: bool) -> str:
    if kwik:
        try:
            pg.wait_for_function(
                """() => {
                    const html = document.documentElement.innerHTML;
                    return html.includes('.m3u8') || html.includes('eval(function(p,a,c,k,e,d)');
                }""",
                timeout=30_000,
            )
        except Exception:
            pg.wait_for_timeout(2000)
    else:
        try:
            pg.wait_for_function(
                """() => document.querySelector('[data-src][data-resolution]')
                    || /https:\\/\\/kwik\\.[a-z]+\\/e\\//i.test(document.body.innerHTML)""",
                timeout=30_000,
            )
        except Exception:
            pg.wait_for_timeout(2000)
    return pg.content()


def _load_url(url: str, *, referer: str | None = None, kwik: bool = False) -> str:
    pg = _page()
    _ensure_pahe_session(pg)
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    pg.set_extra_http_headers(headers)
    pg.goto(url, wait_until="domcontentloaded", timeout=120_000)
    html = _wait_for_html(pg, kwik=kwik)
    title = pg.title() or ""
    if _is_cf(title, html):
        raise RuntimeError(
            "Cloudflare blocked this request. Use Settings -> Pass AnimePahe "
            "Cloudflare first, then retry."
        )
    return html


def _open_kwik_and_capture_m3u8(pg, kwik_url: str, *, referer: str) -> str:
    captured: list[str] = []

    def on_response(resp) -> None:
        if ".m3u8" in resp.url and resp.ok and resp.url not in captured:
            captured.append(resp.url)

    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    pg.set_extra_http_headers(headers)
    pg.on("response", on_response)
    try:
        pg.goto(kwik_url, wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if captured:
                return captured[0]
            pg.wait_for_timeout(500)
        from anidex.services.kwik import extract_m3u8

        html = pg.content()
        m3u8 = extract_m3u8(html)
        if m3u8:
            return m3u8
        raise RuntimeError("Could not extract m3u8 from Kwik page.")
    finally:
        pg.remove_listener("response", on_response)


def close_browser() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            _worker.stop()
        _worker = None


def cloudflare_cleared() -> bool:
    def _check() -> bool:
        pg = _page()
        pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=120_000)
        pg.wait_for_timeout(2000)
        return not _is_cf(pg.title(), pg.content())

    return _worker_instance().submit(_check, timeout=120)


def wait_for_cloudflare(timeout_ms: int = 180_000) -> bool:
    def _wait() -> bool:
        pg = _page()
        pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            pg.wait_for_function(
                """() => {
                    const t = document.title.toLowerCase();
                    const h = document.documentElement.innerHTML.slice(0, 3000).toLowerCase();
                    return !t.includes('just a moment') && !t.includes('attention')
                        && !h.includes('cf-browser-verification');
                }""",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return not _is_cf(pg.title(), pg.content())

    sec = max(30, timeout_ms // 1000 + 10)
    return _worker_instance().submit(_wait, timeout=sec)


def browser_fetch(url: str, *, referer: str = PAHE_HOME, kwik: bool = False) -> str:
    def _fetch() -> str:
        return _load_url(url, referer=referer, kwik=kwik)

    return _worker_instance().submit(_fetch, timeout=180)


def browser_api_json(path: str) -> Any:
    """GET a same-origin AnimePahe API path (e.g. ``/api?m=search&q=...``)."""

    def _fetch() -> Any:
        pg = _page()
        _ensure_pahe_session(pg)
        if "animepahe." not in (pg.url or ""):
            pg.goto(PAHE_HOME, wait_until="domcontentloaded", timeout=120_000)
        data = pg.evaluate(
            """async (path) => {
              const r = await fetch(path, { credentials: 'same-origin' });
              if (!r.ok) {
                return { __error: true, status: r.status, text: await r.text() };
              }
              return await r.json();
            }""",
            path,
        )
        if isinstance(data, dict) and data.get("__error"):
            raise RuntimeError(
                f"AnimePahe API {path} failed: {data.get('status')} {data.get('text')}"
            )
        return data

    return _worker_instance().submit(_fetch, timeout=120)


def browser_resolve_kwik_m3u8(kwik_url: str, *, referer: str = PAHE_HOME) -> str:
    def _resolve() -> str:
        pg = _page()
        _ensure_pahe_session(pg)
        return _open_kwik_and_capture_m3u8(pg, kwik_url, referer=referer)

    return _worker_instance().submit(_resolve, timeout=180)


def browser_download_episode(
    url: str,
    output: Path | None,
    *,
    out_dir: Path | None = None,
    resolution: int = 1080,
    audio: str = "jpn",
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_meta: Callable[[Any], None] | None = None,
):
    """Resolve stream URL in Chrome, then download via curl_cffi (Chrome TLS).

    Returns ``DownloadResult`` (path + AnimePahe metadata).
    """

    def _resolve() -> tuple[str, str, Path, Any, Any]:
        from anidex.services.animepahe import (
            PahePlayMeta,
            PaheSource,
            enrich_meta_from_release_api,
            is_kwik_url,
            parse_play_meta,
            parse_sources,
            pick_source,
        )
        from anidex.services.kwik import extract_m3u8
        from anidex.paths import output_dir
        import re as _re

        def log(msg: str) -> None:
            if on_log:
                on_log(msg)

        def safe_name(name: str) -> str:
            return _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "episode"

        pg = _page()
        _ensure_pahe_session(pg)

        source: PaheSource | None = None
        meta: PahePlayMeta | None = None
        text = url.strip()
        if is_kwik_url(text):
            kwik_url = text
            play_referer = PAHE_HOME
        else:
            log("Loading play page...")
            html = _load_url(text, referer=PAHE_HOME, kwik=False)
            meta = parse_play_meta(html, play_url=text)
            if meta.anime_session:
                try:
                    api = pg.evaluate(
                        """async (session) => {
                          const r = await fetch(
                            `/api?m=release&id=${session}&sort=episode_asc&page=1`
                          );
                          if (!r.ok) return null;
                          return await r.json();
                        }""",
                        meta.anime_session,
                    )
                    if api:
                        meta = enrich_meta_from_release_api(meta, api)
                except Exception:
                    pass
            if on_meta and meta:
                on_meta(meta)
            if meta and meta.anime_title:
                log(f"Detected: {meta.display}")
            sources = parse_sources(html)
            source = pick_source(sources, resolution=resolution, audio=audio)
            kwik_url = source.url
            play_referer = text
            log(f"Selected {source.label}")

        root = out_dir or (output_dir() / "anime")
        if meta and meta.anime_title and output is None:
            show_dir = root / safe_name(meta.anime_title)
            show_dir.mkdir(parents=True, exist_ok=True)
            dest = show_dir / f"{safe_name(meta.filename_stem(source))}.mp4"
        else:
            root.mkdir(parents=True, exist_ok=True)
            if output is None:
                stem = (
                    meta.filename_stem(source)
                    if meta and meta.anime_title
                    else (source.label if source else "episode")
                )
                dest = root / f"{safe_name(stem)}.mp4"
            else:
                dest = Path(output)
                dest.parent.mkdir(parents=True, exist_ok=True)

        log("Opening Kwik player...")
        headers: dict[str, str] = {}
        if play_referer:
            headers["Referer"] = play_referer
        pg.set_extra_http_headers(headers)

        m3u8_urls: list[str] = []

        def on_request(req) -> None:
            if ".m3u8" in req.url.lower() and req.url not in m3u8_urls:
                m3u8_urls.append(req.url)

        pg.on("request", on_request)
        try:
            pg.goto(kwik_url, wait_until="domcontentloaded", timeout=120_000)
            pg.wait_for_timeout(2000)
            m3u8 = extract_m3u8(pg.content())
            if not m3u8:
                for _ in range(30):
                    if m3u8_urls:
                        m3u8 = m3u8_urls[0]
                        break
                    pg.wait_for_timeout(400)
            if not m3u8 and m3u8_urls:
                m3u8 = m3u8_urls[0]
            if not m3u8:
                raise RuntimeError(
                    "Could not find stream URL on Kwik page. "
                    "Make sure Chrome shows the video page (not an ad)."
                )
            log("Stream URL found — downloading…")
            return m3u8, kwik_url, dest, meta, source
        finally:
            pg.remove_listener("request", on_request)

    m3u8_url, kwik_url, dest, meta, source = _worker_instance().submit(
        _resolve, timeout=300
    )
    from anidex.services.animepahe import DownloadResult
    from anidex.services.hls_download import download_hls_curl

    path = download_hls_curl(
        m3u8_url,
        dest,
        referer=kwik_url,
        on_log=on_log,
        on_progress=on_progress,
    )
    return DownloadResult(path=path, meta=meta, source=source)


def browser_download_hls(
    m3u8_url: str,
    output: Path,
    *,
    referer: str,
    kwik_url: str | None = None,
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    from anidex.services.hls_download import download_hls_curl

    return download_hls_curl(
        m3u8_url,
        Path(output),
        referer=kwik_url or referer,
        on_log=on_log,
        on_progress=on_progress,
    )
