from __future__ import annotations

import os
import shutil
from pathlib import Path


def _legacy_app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "LocalFun"


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / "AniDex"
    legacy = _legacy_app_data_dir()
    if not path.exists() and legacy.is_dir():
        try:
            shutil.copytree(legacy, path)
        except OSError:
            path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    # Migrate old DB filename if present
    old_db = path / "localfun.db"
    new_db = path / "anidex.db"
    if old_db.is_file() and not new_db.exists():
        try:
            old_db.rename(new_db)
        except OSError:
            pass
    return path


def db_path() -> Path:
    return app_data_dir() / "anidex.db"


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
