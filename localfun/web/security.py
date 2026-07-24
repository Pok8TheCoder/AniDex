"""Local-network access control for LocalFun.

Loopback (127.0.0.1 / ::1) is always allowed.
Non-loopback clients are only allowed when LAN mode is enabled and they
present the access token (cookie, header, or ?token= query once).
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

COOKIE = "lf_token"
HEADER = "X-LocalFun-Token"

_lan_mode = False
_token: str | None = None


def enable_lan_mode(token: str | None = None) -> str:
    """Allow remote LAN clients that present the returned token."""
    global _lan_mode, _token
    _lan_mode = True
    _token = token or os.environ.get("LOCALFUN_TOKEN") or secrets.token_urlsafe(24)
    os.environ["LOCALFUN_LAN"] = "1"
    os.environ["LOCALFUN_TOKEN"] = _token
    return _token


def lan_enabled() -> bool:
    return _lan_mode or os.environ.get("LOCALFUN_LAN") == "1"


def access_token() -> str | None:
    return _token or os.environ.get("LOCALFUN_TOKEN")


def is_loopback_host(host: str | None) -> bool:
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


def request_token(request: Request) -> str | None:
    return (
        request.cookies.get(COOKIE)
        or request.headers.get(HEADER)
        or request.query_params.get("token")
    )


def token_ok(request: Request) -> bool:
    expected = access_token()
    got = request_token(request)
    if not expected or not got:
        return False
    try:
        return secrets.compare_digest(got, expected)
    except (TypeError, ValueError):
        return False


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


_DENY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LocalFun</title>
<style>body{font-family:system-ui;background:#141210;color:#f0e6d8;padding:40px;max-width:520px;margin:auto}
code{background:#2a2420;padding:2px 6px;border-radius:4px}</style></head>
<body>
<h1>Access blocked</h1>
<p>This LocalFun instance only accepts localhost, or a one-time LAN link with an access token.</p>
<p>On the PC that runs the app, start with <code>python app.py --lan</code> and open the printed phone URL.</p>
</body></html>
"""


class LocalAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if client_is_loopback(request):
            return await call_next(request)

        # Remote client
        if not lan_enabled():
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Remote access disabled. Start with --lan."},
                    status_code=403,
                )
            return HTMLResponse(_DENY_HTML, status_code=403)

        if not token_ok(request):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {
                        "detail": "Unauthorized. Open the token URL printed on the PC console."
                    },
                    status_code=401,
                )
            return HTMLResponse(_DENY_HTML, status_code=401)

        response = await call_next(request)
        # Persist token after first ?token= visit so subsequent asset/API calls work
        qtok = request.query_params.get("token")
        expected = access_token()
        if qtok and expected and secrets.compare_digest(qtok, expected):
            response.set_cookie(
                COOKIE,
                expected,
                max_age=7 * 24 * 3600,
                httponly=False,
                samesite="lax",
                path="/",
            )
        return response
