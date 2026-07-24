"""HTTP helpers for Termux → PC peer Pahe operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from anidex.sync import live as live_sync
from anidex.sync.token import ensure_sync_identity


def peer_base_and_headers() -> tuple[str, dict[str, str]]:
    ident = ensure_sync_identity()
    peer = (ident.get("peer_url") or "").rstrip("/")
    token = ident.get("sync_token") or ""
    if not peer or not token:
        raise RuntimeError(
            "No peer configured. Set Peer URL + sync token in Settings → Peer sync."
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-AniDex-Sync-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "AniDex-Peer/1.0",
    }
    return peer, headers


def ensure_peer_can_pahe() -> None:
    if live_sync.is_connected() and live_sync.peer_has("remote_pahe"):
        return
    if live_sync.peer_has("playwright") or live_sync.peer_has("remote_download"):
        return
    peer, headers = peer_base_and_headers()
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        r = client.get(urljoin(peer + "/", "api/sync/hello"), headers=headers)
        if r.status_code != 200:
            raise RuntimeError("Peer not reachable. Start PC with --lan and Sync now.")
        caps = (r.json() or {}).get("capabilities") or []
        if "remote_pahe" not in caps and "playwright" not in caps:
            raise RuntimeError(
                "Peer cannot talk to AnimePahe (install Playwright on the PC)."
            )


def peer_post(path: str, json_body: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
    ensure_peer_can_pahe()
    peer, headers = peer_base_and_headers()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.post(urljoin(peer + "/", path.lstrip("/")), headers=headers, json=json_body)
        if r.status_code >= 400:
            detail = r.text
            try:
                detail = r.json().get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(str(detail))
        return r.json() if r.content else {}


def peer_get(path: str, *, timeout: float = 120.0) -> Any:
    ensure_peer_can_pahe()
    peer, headers = peer_base_and_headers()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(urljoin(peer + "/", path.lstrip("/")), headers=headers)
        if r.status_code >= 400:
            detail = r.text
            try:
                detail = r.json().get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(str(detail))
        return r.json() if r.content else {}
