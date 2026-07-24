"""Live peer mesh: persistent WebSocket + auto sync while connected."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from anidex.sync.capabilities import local_capabilities
from anidex.sync.token import ensure_sync_identity

log = logging.getLogger("anidex.sync.live")

_DEBOUNCE_S = 2.0
_PING_S = 15.0
_BACKOFF_START = 1.0
_BACKOFF_MAX = 30.0

_lock = threading.RLock()
_sync_lock = threading.Lock()
_syncing = False

_state = "offline"  # offline|connecting|connected|reconnecting
_peer_device_id = ""
_peer_capabilities: list[str] = []
_last_sync_at: float | None = None
_last_error = ""
_connected = False

_loop: asyncio.AbstractEventLoop | None = None
_supervisor_task: asyncio.Task | None = None
_dirty_event: asyncio.Event | None = None
_inbound: set[Any] = set()  # WebSocket
_outbound: Any | None = None  # websockets connection
_stop = False


def status() -> dict[str, Any]:
    with _lock:
        return {
            "state": _state,
            "connected": _connected,
            "peer_device_id": _peer_device_id,
            "peer_capabilities": list(_peer_capabilities),
            "last_sync_at": _last_sync_at,
            "last_error": _last_error,
            "local_capabilities": local_capabilities(),
        }


def is_connected() -> bool:
    with _lock:
        return _connected


def peer_has(capability: str) -> bool:
    with _lock:
        return capability in _peer_capabilities


def is_syncing() -> bool:
    return _syncing


def _set_state(
    state: str,
    *,
    error: str | None = None,
    peer_device_id: str | None = None,
    peer_caps: list[str] | None = None,
    connected: bool | None = None,
) -> None:
    global _state, _last_error, _peer_device_id, _peer_capabilities, _connected
    with _lock:
        _state = state
        if error is not None:
            _last_error = error
        if peer_device_id is not None:
            _peer_device_id = peer_device_id
        if peer_caps is not None:
            _peer_capabilities = peer_caps
        if connected is not None:
            _connected = connected


def mark_dirty(*, reason: str = "") -> None:
    """Schedule a debounced sync and notify connected peers."""
    if _syncing:
        return
    if _dirty_event is not None and _loop is not None:
        def _set() -> None:
            assert _dirty_event is not None
            _dirty_event.set()

        try:
            _loop.call_soon_threadsafe(_set)
        except RuntimeError:
            pass
    _broadcast({"type": "sync_needed", "reason": reason or "local_change"})


def notify_download_done(
    *,
    mal_id: int | None = None,
    episode: float | None = None,
    key: str = "",
) -> None:
    _broadcast(
        {
            "type": "download_done",
            "mal_id": mal_id,
            "episode": episode,
            "key": key,
        }
    )
    mark_dirty(reason="download_done")


def _broadcast(msg: dict[str, Any]) -> None:
    raw = json.dumps(msg)
    if _loop is None:
        return

    async def _send_all() -> None:
        dead = []
        for ws in list(_inbound):
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _inbound.discard(ws)
        out = _outbound
        if out is not None:
            try:
                await out.send(raw)
            except Exception:
                pass

    try:
        asyncio.run_coroutine_threadsafe(_send_all(), _loop)
    except RuntimeError:
        pass


def run_sync_safe(peer_url: str | None = None) -> dict[str, Any] | None:
    """Run mesh sync once; skips if already syncing."""
    global _syncing, _last_sync_at, _last_error
    if not _sync_lock.acquire(blocking=False):
        return None
    try:
        _syncing = True
        from anidex.sync.client import run_sync

        result = run_sync(peer_url)
        with _lock:
            _last_sync_at = time.time()
            _last_error = ""
        return result
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _last_error = str(exc)
        log.warning("live sync failed: %s", exc)
        return None
    finally:
        _syncing = False
        _sync_lock.release()


def _http_to_ws(peer: str) -> str:
    p = urlparse(peer.rstrip("/") + "/")
    scheme = "wss" if p.scheme == "https" else "ws"
    return urlunparse((scheme, p.netloc, "/api/sync/ws", "", "", ""))


def _probe_hello(peer: str, token: str) -> dict[str, Any] | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-AniDex-Sync-Token": token,
        "User-Agent": "AniDex-Live/1.0",
    }
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            r = client.get(urljoin(peer.rstrip("/") + "/", "api/sync/hello"), headers=headers)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("probe failed: %s", exc)
        return None


async def handle_inbound(websocket: Any) -> None:
    """Serve an accepted FastAPI WebSocket from a peer."""
    ident = ensure_sync_identity()
    await websocket.send_text(
        json.dumps(
            {
                "type": "hello",
                "device_id": ident["device_id"],
                "capabilities": local_capabilities(),
            }
        )
    )
    _inbound.add(websocket)
    _set_state("connected", connected=True, error="")
    # Sync when a peer connects to us
    asyncio.get_running_loop().run_in_executor(None, run_sync_safe)
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(raw, reply_ws=websocket)
    except Exception:
        pass
    finally:
        _inbound.discard(websocket)
        if not _inbound and _outbound is None:
            _set_state("offline", connected=False)


async def _handle_message(raw: str, reply_ws: Any | None = None) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type")
    if mtype == "hello":
        _set_state(
            "connected",
            peer_device_id=str(msg.get("device_id") or ""),
            peer_caps=list(msg.get("capabilities") or []),
            connected=True,
            error="",
        )
    elif mtype == "ping" and reply_ws is not None:
        try:
            if hasattr(reply_ws, "send_text"):
                await reply_ws.send_text(json.dumps({"type": "pong"}))
            else:
                await reply_ws.send(json.dumps({"type": "pong"}))
        except Exception:
            pass
    elif mtype == "pong":
        pass
    elif mtype in ("sync_needed", "download_done"):
        if _dirty_event is not None:
            _dirty_event.set()


async def _outbound_session(peer: str, token: str) -> None:
    global _outbound
    try:
        import websockets
    except ImportError:
        _set_state("offline", error="websockets package missing", connected=False)
        return

    uri = _http_to_ws(peer) + f"?token={token}"
    _set_state("connecting", connected=False)
    try:
        # websockets API: extra_headers (legacy) or additional_headers
        connect_kwargs: dict[str, Any] = {
            "ping_interval": None,
            "open_timeout": 10,
        }
        try:
            async with websockets.connect(
                uri,
                additional_headers={
                    "Authorization": f"Bearer {token}",
                    "X-AniDex-Sync-Token": token,
                },
                **connect_kwargs,
            ) as ws:
                await _run_outbound_ws(ws, peer)
        except TypeError:
            async with websockets.connect(
                uri,
                extra_headers={
                    "Authorization": f"Bearer {token}",
                    "X-AniDex-Sync-Token": token,
                },
                **connect_kwargs,
            ) as ws:
                await _run_outbound_ws(ws, peer)
    finally:
        _outbound = None
        if not _inbound:
            _set_state("offline", connected=False)


async def _run_outbound_ws(ws: Any, peer: str) -> None:
    global _outbound
    _outbound = ws
    ident = ensure_sync_identity()
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "device_id": ident["device_id"],
                "capabilities": local_capabilities(),
            }
        )
    )
    _set_state("connected", connected=True, error="")
    asyncio.get_running_loop().run_in_executor(None, run_sync_safe)

    async def reader() -> None:
        async for raw in ws:
            text = raw if isinstance(raw, str) else raw.decode()
            await _handle_message(text, reply_ws=ws)

    async def pinger() -> None:
        while True:
            await asyncio.sleep(_PING_S)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                return

    reader_t = asyncio.create_task(reader())
    ping_t = asyncio.create_task(pinger())
    done, pending = await asyncio.wait(
        {reader_t, ping_t}, return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc:
            raise exc


async def _dirty_loop() -> None:
    assert _dirty_event is not None
    while not _stop:
        await _dirty_event.wait()
        _dirty_event.clear()
        await asyncio.sleep(_DEBOUNCE_S)
        # coalesce
        while _dirty_event.is_set():
            _dirty_event.clear()
            await asyncio.sleep(0.2)
        if _stop:
            break
        await asyncio.get_running_loop().run_in_executor(None, run_sync_safe)


async def _supervisor() -> None:
    global _dirty_event
    _dirty_event = asyncio.Event()
    dirty_task = asyncio.create_task(_dirty_loop())
    backoff = _BACKOFF_START
    try:
        while not _stop:
            ident = ensure_sync_identity()
            peer = (ident.get("peer_url") or "").rstrip("/")
            token = ident.get("sync_token") or ""
            if not peer or not token:
                _set_state("offline", connected=False, error="No peer URL or sync token")
                await asyncio.sleep(5)
                continue

            hello = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _probe_hello(peer, token)
            )
            if not hello:
                _set_state(
                    "reconnecting",
                    connected=False,
                    error="Peer not reachable",
                )
                await asyncio.sleep(backoff)
                backoff = min(_BACKOFF_MAX, backoff * 2)
                continue

            _set_state(
                "connecting",
                peer_device_id=str(hello.get("device_id") or ""),
                peer_caps=list(hello.get("capabilities") or []),
                error="",
            )
            try:
                await _outbound_session(peer, token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _set_state("reconnecting", connected=False, error=str(exc))
                log.info("peer ws closed: %s", exc)

            if _stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(_BACKOFF_MAX, backoff * 2) if not is_connected() else _BACKOFF_START
            if is_connected():
                backoff = _BACKOFF_START
    finally:
        dirty_task.cancel()
        try:
            await dirty_task
        except asyncio.CancelledError:
            pass


def start() -> None:
    """Start live supervisor on the running asyncio loop (call from lifespan)."""
    global _loop, _supervisor_task, _stop
    _stop = False
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("live.start() requires a running event loop")
        return
    if _supervisor_task and not _supervisor_task.done():
        return
    _supervisor_task = _loop.create_task(_supervisor(), name="anidex-live")


async def stop() -> None:
    global _stop, _supervisor_task, _outbound
    _stop = True
    if _dirty_event is not None:
        _dirty_event.set()
    out = _outbound
    if out is not None:
        try:
            await out.close()
        except Exception:
            pass
    for ws in list(_inbound):
        try:
            await ws.close()
        except Exception:
            pass
        _inbound.discard(ws)
    if _supervisor_task is not None:
        _supervisor_task.cancel()
        try:
            await _supervisor_task
        except asyncio.CancelledError:
            pass
        _supervisor_task = None
    _set_state("offline", connected=False)


def kick() -> None:
    """Force reconnect soon (e.g. after settings change)."""
    mark_dirty(reason="settings")
    # Reset by closing outbound
    if _loop is not None and _outbound is not None:
        async def _close() -> None:
            out = _outbound
            if out is not None:
                try:
                    await out.close()
                except Exception:
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_close(), _loop)
        except RuntimeError:
            pass
