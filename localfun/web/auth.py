"""LocalFun login: credentials in DB, signed sessions, IP lockout."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any
from urllib.parse import parse_qsl, urlencode

from localfun.db.schema import open_repo
from localfun.web.passwords import hash_password, verify_password

COOKIE = "lf_session"
HEADER = "X-LocalFun-Session"
MAX_ATTEMPTS = 3
SESSION_DAYS = 7
DEFAULT_USERNAME = "user"
DEFAULT_PASSWORD = "pwd"


def _b64e(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return urlsafe_b64decode(text + pad)


def ensure_auth_defaults() -> None:
    """Create default user/pwd hash + session secret if missing."""
    conn, repo = open_repo()
    try:
        row = conn.execute(
            "SELECT auth_username, password_hash, session_secret FROM profile WHERE id=1"
        ).fetchone()
        if not row:
            return
        username = (row["auth_username"] or "").strip() or DEFAULT_USERNAME
        pwd_hash = row["password_hash"] or ""
        secret = row["session_secret"] or ""
        changed = False
        if not pwd_hash:
            pwd_hash = hash_password(DEFAULT_PASSWORD)
            changed = True
        if not secret:
            secret = secrets.token_urlsafe(32)
            changed = True
        if not (row["auth_username"] or "").strip():
            changed = True
        if changed:
            conn.execute(
                """
                UPDATE profile SET auth_username=?, password_hash=?, session_secret=?,
                updated_at=datetime('now') WHERE id=1
                """,
                (username, pwd_hash, secret),
            )
            conn.commit()
    finally:
        conn.close()


def _auth_row() -> dict[str, Any]:
    ensure_auth_defaults()
    conn, _ = open_repo()
    try:
        row = conn.execute(
            "SELECT auth_username, password_hash, session_secret FROM profile WHERE id=1"
        ).fetchone()
        return {
            "username": (row["auth_username"] or DEFAULT_USERNAME).strip(),
            "password_hash": row["password_hash"] or "",
            "session_secret": row["session_secret"] or "",
        }
    finally:
        conn.close()


def get_auth_username() -> str:
    return _auth_row()["username"]


def verify_credentials(username: str, password: str) -> bool:
    row = _auth_row()
    if not username or not password:
        return False
    # Username match need not be constant-time for a single-user local app
    if username.strip() != row["username"]:
        verify_password(password, row["password_hash"] or (
            "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        ))
        return False
    return verify_password(password, row["password_hash"])


def update_credentials(
    *,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Update username and/or password. Returns new username. Rotates session secret."""
    row = _auth_row()
    new_user = (username if username is not None else row["username"]).strip()
    if not new_user:
        raise ValueError("Username cannot be empty")
    if len(new_user) > 64:
        raise ValueError("Username too long")
    pwd_hash = row["password_hash"]
    if password is not None:
        if len(password) < 1:
            raise ValueError("Password cannot be empty")
        if len(password) > 256:
            raise ValueError("Password too long")
        pwd_hash = hash_password(password)
    secret = secrets.token_urlsafe(32)
    conn, _ = open_repo()
    try:
        conn.execute(
            """
            UPDATE profile SET auth_username=?, password_hash=?, session_secret=?,
            updated_at=datetime('now') WHERE id=1
            """,
            (new_user, pwd_hash, secret),
        )
        conn.commit()
    finally:
        conn.close()
    return new_user


def issue_session(username: str) -> str:
    """Create a signed session cookie value."""
    secret = _auth_row()["session_secret"].encode("utf-8")
    exp = int(time.time()) + SESSION_DAYS * 24 * 3600
    nonce = secrets.token_urlsafe(8)
    payload = urlencode({"u": username, "exp": exp, "n": nonce})
    payload_b64 = _b64e(payload.encode("utf-8"))
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"v1.{payload_b64}.{_b64e(sig)}"


def session_username(token: str | None) -> str | None:
    if not token or not token.startswith("v1."):
        return None
    try:
        _, payload_b64, sig_b64 = token.split(".", 2)
        secret = _auth_row()["session_secret"].encode("utf-8")
        expected = hmac.new(
            secret, payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        data = dict(parse_qsl(_b64d(payload_b64).decode("utf-8")))
        if int(data.get("exp", "0")) < int(time.time()):
            return None
        username = data.get("u") or ""
        if username != _auth_row()["username"]:
            return None
        return username
    except (ValueError, TypeError, KeyError):
        return None


# ---- IP lockout (persisted) ----

def lockout_status(ip: str) -> dict[str, Any]:
    if not ip:
        return {"fail_count": 0, "locked": False}
    conn, _ = open_repo()
    try:
        row = conn.execute(
            "SELECT fail_count, locked FROM auth_lockout WHERE ip=?", (ip,)
        ).fetchone()
        if not row:
            return {"fail_count": 0, "locked": False}
        return {"fail_count": int(row["fail_count"]), "locked": bool(row["locked"])}
    finally:
        conn.close()


def is_ip_locked(ip: str) -> bool:
    return bool(lockout_status(ip)["locked"])


def record_login_failure(ip: str) -> dict[str, Any]:
    """Increment fail count; lock at MAX_ATTEMPTS. Returns status."""
    if not ip:
        return {"fail_count": 0, "locked": False, "remaining": MAX_ATTEMPTS}
    conn, _ = open_repo()
    try:
        row = conn.execute(
            "SELECT fail_count, locked FROM auth_lockout WHERE ip=?", (ip,)
        ).fetchone()
        if row and row["locked"]:
            return {
                "fail_count": int(row["fail_count"]),
                "locked": True,
                "remaining": 0,
            }
        fails = (int(row["fail_count"]) if row else 0) + 1
        locked = 1 if fails >= MAX_ATTEMPTS else 0
        conn.execute(
            """
            INSERT INTO auth_lockout (ip, fail_count, locked, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(ip) DO UPDATE SET
              fail_count=excluded.fail_count,
              locked=excluded.locked,
              updated_at=datetime('now')
            """,
            (ip, fails, locked),
        )
        conn.commit()
        return {
            "fail_count": fails,
            "locked": bool(locked),
            "remaining": max(0, MAX_ATTEMPTS - fails),
        }
    finally:
        conn.close()


def clear_login_failures(ip: str) -> None:
    if not ip:
        return
    conn, _ = open_repo()
    try:
        conn.execute("DELETE FROM auth_lockout WHERE ip=?", (ip,))
        conn.commit()
    finally:
        conn.close()


def list_lockouts() -> list[dict[str, Any]]:
    conn, _ = open_repo()
    try:
        rows = conn.execute(
            "SELECT ip, fail_count, locked, updated_at FROM auth_lockout ORDER BY updated_at DESC"
        ).fetchall()
        return [
            {
                "ip": r["ip"],
                "fail_count": int(r["fail_count"]),
                "locked": bool(r["locked"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def unlock_ip(ip: str | None = None) -> int:
    """Clear one IP or all lockouts. Returns rows deleted."""
    conn, _ = open_repo()
    try:
        if ip:
            cur = conn.execute("DELETE FROM auth_lockout WHERE ip=?", (ip,))
        else:
            cur = conn.execute("DELETE FROM auth_lockout")
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
