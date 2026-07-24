"""Local-network access control for AniDex.

Loopback (127.0.0.1 / ::1) is always allowed.
Non-loopback clients need LAN mode + a logged-in session cookie.
Failed logins lock the client IP after 3 attempts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from anidex.web import auth as lf_auth

COOKIE = lf_auth.COOKIE
HEADER = lf_auth.HEADER

_lan_mode = False

# Paths reachable without a session (remote clients still need LAN mode)
_PUBLIC_PREFIXES = (
    "/login",
    "/static/",
    "/api/auth/login",
    "/api/auth/status",
)
_PUBLIC_EXACT = {"/favicon.ico"}


def enable_lan_mode() -> None:
    """Allow remote LAN clients that log in with username/password."""
    global _lan_mode
    _lan_mode = True
    os.environ["ANIDEX_LAN"] = "1"


def sync_lan_from_env() -> None:
    if os.environ.get("ANIDEX_LAN") == "1" or os.environ.get("LOCALFUN_LAN") == "1":
        enable_lan_mode()


def lan_enabled() -> bool:
    sync_lan_from_env()
    return (
        _lan_mode
        or os.environ.get("ANIDEX_LAN") == "1"
        or os.environ.get("LOCALFUN_LAN") == "1"
    )


def is_loopback_host(host: str | None) -> bool:
    import ipaddress

    if not host:
        return False
    host = host.split("%", 1)[0].strip("[]")
    if host in {"localhost", "127.0.0.1", "::1", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    return is_loopback_host(host)


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    return host or ""


def request_session(request: Request) -> str | None:
    return (
        request.cookies.get(COOKIE)
        or request.cookies.get("lf_session")  # legacy LocalFun cookie
        or request.headers.get(HEADER)
        or request.headers.get("X-LocalFun-Session")
    )


def session_ok(request: Request) -> bool:
    return lf_auth.session_username(request_session(request)) is not None


def redact_path(path: str | Path | None) -> str:
    """Return basename only — never leak home / username directories."""
    if not path:
        return ""
    return Path(path).name


def redact_job_result(result: Any) -> Any:
    if isinstance(result, dict):
        out = dict(result)
        if "path" in out and out["path"]:
            out["path"] = redact_path(out["path"])
        return out
    return result


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)


def _locked_html(ip: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AniDex — locked</title>
<style>
body{{font-family:system-ui,sans-serif;background:#141210;color:#f0e6d8;padding:28px;max-width:520px;margin:auto;line-height:1.45}}
code{{background:#2a2420;padding:2px 6px;border-radius:4px}}
</style></head>
<body>
<h1>IP locked</h1>
<p>Too many failed login attempts from <code>{ip}</code>.</p>
<p>On the PC running AniDex, open Settings → Login and unlock this IP
(or restart after clearing lockouts).</p>
</body></html>
"""


def _deny_lan_html() -> str:
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AniDex</title>
<style>
body{font-family:system-ui,sans-serif;background:#141210;color:#f0e6d8;padding:28px;max-width:520px;margin:auto;line-height:1.45}
code{background:#2a2420;padding:2px 6px;border-radius:4px}
</style></head>
<body>
<h1>Access blocked</h1>
<p>This AniDex instance only accepts localhost.</p>
<p>On the PC, restart with <code>python app.py --lan</code> and open the printed phone URL, then log in.</p>
</body></html>
"""


class LocalAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        ip = client_ip(request)

        if client_is_loopback(request):
            return await call_next(request)

        if not lan_enabled():
            if path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Remote access disabled. Start with --lan."},
                    status_code=403,
                )
            return HTMLResponse(_deny_lan_html(), status_code=403)

        # Locked IPs cannot reach anything except static assets for the locked page
        if lf_auth.is_ip_locked(ip) and not path.startswith("/static/"):
            if path.startswith("/api/"):
                return JSONResponse(
                    {
                        "detail": "IP locked after too many failed logins. "
                        "Unlock from Settings on the PC."
                    },
                    status_code=403,
                )
            return HTMLResponse(_locked_html(ip), status_code=403)

        if _is_public(path):
            return await call_next(request)

        # Peer sync uses shared sync token (not UI login session)
        if path.startswith("/api/sync/") and path not in {
            "/api/sync/status",
            "/api/sync/settings",
            "/api/sync/rotate-token",
            "/api/sync/run",
        }:
            from anidex.sync.token import sync_token_ok

            auth = request.headers.get("authorization") or ""
            presented = (
                request.headers.get("x-anidex-sync-token")
                or request.headers.get("x-localfun-sync-token")
                or (auth[7:].strip() if auth.lower().startswith("bearer ") else None)
                or request.query_params.get("token")
            )
            if sync_token_ok(presented):
                return await call_next(request)

        if session_ok(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Login required. Open /login on this device."},
                status_code=401,
            )

        nxt = path
        if request.url.query:
            nxt = f"{path}?{request.url.query}"
        return RedirectResponse(
            f"/login?next={quote(nxt or '/', safe='')}",
            status_code=302,
        )
