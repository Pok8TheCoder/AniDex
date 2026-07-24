"""Device identity and shared peer sync token."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from anidex.db.schema import open_repo


def ensure_sync_identity() -> dict[str, str]:
    """Ensure device_id + sync_token exist; return them."""
    conn, _ = open_repo()
    try:
        row = conn.execute(
            "SELECT device_id, sync_token, peer_url FROM profile WHERE id=1"
        ).fetchone()
        if not row:
            return {"device_id": "", "sync_token": "", "peer_url": ""}
        device_id = (row["device_id"] or "").strip()
        sync_token = (row["sync_token"] or "").strip()
        peer_url = (row["peer_url"] or "").strip()
        changed = False
        if not device_id:
            device_id = str(uuid.uuid4())
            changed = True
        if not sync_token:
            sync_token = secrets.token_urlsafe(24)
            changed = True
        if changed:
            conn.execute(
                """
                UPDATE profile SET device_id=?, sync_token=?, updated_at=datetime('now')
                WHERE id=1
                """,
                (device_id, sync_token),
            )
            conn.commit()
        return {"device_id": device_id, "sync_token": sync_token, "peer_url": peer_url}
    finally:
        conn.close()


def get_sync_identity() -> dict[str, str]:
    return ensure_sync_identity()


def rotate_sync_token() -> str:
    token = secrets.token_urlsafe(24)
    conn, _ = open_repo()
    try:
        ensure_sync_identity()
        conn.execute(
            "UPDATE profile SET sync_token=?, updated_at=datetime('now') WHERE id=1",
            (token,),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def set_peer_url(url: str) -> None:
    conn, _ = open_repo()
    try:
        ensure_sync_identity()
        conn.execute(
            "UPDATE profile SET peer_url=?, updated_at=datetime('now') WHERE id=1",
            ((url or "").strip(),),
        )
        conn.commit()
    finally:
        conn.close()


def set_sync_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("Sync token cannot be empty")
    conn, _ = open_repo()
    try:
        ensure_sync_identity()
        conn.execute(
            "UPDATE profile SET sync_token=?, updated_at=datetime('now') WHERE id=1",
            (token,),
        )
        conn.commit()
    finally:
        conn.close()


def sync_token_ok(presented: str | None) -> bool:
    if not presented:
        return False
    expected = ensure_sync_identity()["sync_token"]
    if not expected:
        return False
    import hmac

    return hmac.compare_digest(presented.strip(), expected)


def add_sync_log(
    *,
    peer: str,
    direction: str,
    message: str,
    counts: dict[str, Any] | None = None,
    ok: bool = True,
) -> None:
    import json

    conn, _ = open_repo()
    try:
        conn.execute(
            """
            INSERT INTO sync_log (peer, direction, message, counts_json, ok, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                peer or "",
                direction or "",
                message or "",
                json.dumps(counts or {}),
                1 if ok else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_sync_logs(limit: int = 20) -> list[dict[str, Any]]:
    import json

    conn, _ = open_repo()
    try:
        rows = conn.execute(
            """
            SELECT id, peer, direction, message, counts_json, ok, created_at
            FROM sync_log ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(100, limit)),),
        ).fetchall()
        out = []
        for r in rows:
            try:
                counts = json.loads(r["counts_json"] or "{}")
            except json.JSONDecodeError:
                counts = {}
            out.append(
                {
                    "id": r["id"],
                    "peer": r["peer"],
                    "direction": r["direction"],
                    "message": r["message"],
                    "counts": counts,
                    "ok": bool(r["ok"]),
                    "created_at": r["created_at"],
                }
            )
        return out
    finally:
        conn.close()
