"""Peer capability probes for live sync / remote download."""

from __future__ import annotations

import importlib.util
from typing import Any


def playwright_available() -> bool:
    """True when Playwright is importable (PC AnimePahe path)."""
    return importlib.util.find_spec("playwright") is not None


def local_capabilities() -> list[str]:
    caps = ["library", "anime_media", "manga_offline", "positions", "live_ws", "pahe_catalog"]
    if playwright_available():
        caps.extend(["playwright", "remote_download", "remote_pahe"])
    return caps


def hello_payload(device_id: str, *, app: str, protocol: int) -> dict[str, Any]:
    return {
        "app": app,
        "protocol": protocol,
        "device_id": device_id,
        "capabilities": local_capabilities(),
    }
