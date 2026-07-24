from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from anidex.db import open_repo
from anidex.db.repositories import Repository


@contextmanager
def repo_ctx() -> Generator[Repository, None, None]:
    conn, repo = open_repo()
    try:
        yield repo
    finally:
        conn.close()
