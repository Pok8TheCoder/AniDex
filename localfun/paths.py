from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / "LocalFun"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return app_data_dir() / "localfun.db"


def cover_cache_dir() -> Path:
    path = app_data_dir() / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir() -> Path:
    # Project-relative output for downloads (manga PDFs etc.)
    root = Path(__file__).resolve().parent.parent
    path = root / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manga_offline_dir(user_manga_id: int | None = None, chapter_id: str | None = None) -> Path:
    path = app_data_dir() / "manga_offline"
    if user_manga_id is not None:
        path = path / str(user_manga_id)
    if chapter_id:
        path = path / chapter_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def tokens_path() -> Path:
    return app_data_dir() / "mal_tokens.json"
