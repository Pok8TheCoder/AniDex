from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_name TEXT NOT NULL DEFAULT '',
    onboarded INTEGER NOT NULL DEFAULT 0,
    mal_username TEXT NOT NULL DEFAULT '',
    mal_client_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS anime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mal_id INTEGER UNIQUE,
    title TEXT NOT NULL,
    title_english TEXT,
    media_type TEXT,
    airing_status TEXT,
    episodes INTEGER,
    mean_score REAL,
    cover_url TEXT,
    synopsis TEXT,
    cached_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manga (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mangadex_id TEXT UNIQUE,
    title TEXT NOT NULL,
    title_english TEXT,
    status TEXT,
    year INTEGER,
    cover_url TEXT,
    synopsis TEXT,
    cached_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_anime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anime_id INTEGER NOT NULL UNIQUE REFERENCES anime(id) ON DELETE CASCADE,
    list_status TEXT NOT NULL DEFAULT 'plan_to_watch',
    progress INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    local_folder TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    mal_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS user_manga (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manga_id INTEGER NOT NULL UNIQUE REFERENCES manga(id) ON DELETE CASCADE,
    list_status TEXT NOT NULL DEFAULT 'plan_to_read',
    progress INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    local_folder TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS local_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL DEFAULT 'anime',
    ref_id INTEGER NOT NULL DEFAULT 0,
    user_anime_id INTEGER REFERENCES user_anime(id) ON DELETE CASCADE,
    pahe_episode_session TEXT NOT NULL DEFAULT '',
    episode REAL,
    path TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS anime_pahe_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_anime_id INTEGER NOT NULL UNIQUE REFERENCES user_anime(id) ON DELETE CASCADE,
    pahe_session TEXT NOT NULL DEFAULT '',
    pahe_title TEXT NOT NULL DEFAULT '',
    poster TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS anime_pahe_season (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id INTEGER NOT NULL REFERENCES anime_pahe_link(id) ON DELETE CASCADE,
    pahe_session TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    year INTEGER,
    episode_count INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(link_id, pahe_session)
);

CREATE TABLE IF NOT EXISTS anime_pahe_episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL REFERENCES anime_pahe_season(id) ON DELETE CASCADE,
    episode REAL NOT NULL,
    episode2 REAL,
    episode_session TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    disc TEXT NOT NULL DEFAULT '',
    UNIQUE(season_id, episode_session)
);

CREATE TABLE IF NOT EXISTS manga_offline_chapter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_manga_id INTEGER NOT NULL REFERENCES user_manga(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL,
    chapter_no TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    quality TEXT NOT NULL DEFAULT 'data-saver',
    bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_manga_id, chapter_id)
);

CREATE INDEX IF NOT EXISTS idx_user_anime_status ON user_anime(list_status);
CREATE INDEX IF NOT EXISTS idx_user_manga_status ON user_manga(list_status);
CREATE INDEX IF NOT EXISTS idx_anime_title ON anime(title);
CREATE INDEX IF NOT EXISTS idx_manga_title ON manga(title);
CREATE INDEX IF NOT EXISTS idx_pahe_ep_session ON anime_pahe_episode(episode_session);
CREATE INDEX IF NOT EXISTS idx_manga_offline_ch ON manga_offline_chapter(chapter_id);
CREATE INDEX IF NOT EXISTS idx_manga_offline_um ON manga_offline_chapter(user_manga_id);
"""

ANIME_STATUSES = (
    "watching",
    "completed",
    "on_hold",
    "dropped",
    "plan_to_watch",
)

MANGA_STATUSES = (
    "reading",
    "completed",
    "on_hold",
    "dropped",
    "plan_to_read",
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    from localfun.paths import db_path

    db = path or db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def open_repo(path: Path | None = None) -> tuple[sqlite3.Connection, "Repository"]:
    """Open a fresh connection + repository (safe for background threads)."""
    from localfun.db.repositories import Repository

    conn = connect(path)
    return conn, Repository(conn)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for existing LocalFun installs."""
    cols = _column_names(conn, "local_media")
    alters = [
        ("user_anime_id", "INTEGER"),
        ("pahe_episode_session", "TEXT NOT NULL DEFAULT ''"),
        ("episode", "REAL"),
        ("bytes", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for name, decl in alters:
        if name not in cols:
            conn.execute(f"ALTER TABLE local_media ADD COLUMN {name} {decl}")
    # Indexes that depend on migrated columns must run after ALTER.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_media_ua ON local_media(user_anime_id)"
    )


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    own = conn is None
    if own:
        conn = connect()
    assert conn is not None
    conn.executescript(SCHEMA)
    _migrate(conn)
    row = conn.execute("SELECT id FROM profile WHERE id = 1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO profile (id, display_name, onboarded) VALUES (1, '', 0)"
        )
    conn.commit()
    return conn
