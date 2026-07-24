from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from anidex.paths import cover_cache_dir


def cover_cache_path(url: str | None) -> Path | None:
    if not url:
        return None
    digest = hashlib.sha1(url.encode()).hexdigest()[:20]
    for ext in (".jpg", ".png", ".webp"):
        candidate = cover_cache_dir() / f"{digest}{ext}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    # Preferred path for a new download (jpg default)
    ext = ".jpg"
    if ".png" in url.lower():
        ext = ".png"
    elif ".webp" in url.lower():
        ext = ".webp"
    return cover_cache_dir() / f"{digest}{ext}"


def cache_cover(url: str | None) -> Path | None:
    if not url:
        return None
    dest = cover_cache_path(url)
    if dest is None:
        return None
    # If any hashed variant exists, cover_cache_path already returned it
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        r = requests.get(
            url,
            timeout=60,
            headers={"User-Agent": "AniDex/0.1"},
        )
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except requests.RequestException:
        return None
