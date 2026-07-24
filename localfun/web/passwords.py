"""Password hashing — PBKDF2-HMAC-SHA256 (no plaintext storage)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

ALGORITHM = "pbkdf2_sha256"
# OWASP-recommended order of magnitude for PBKDF2-SHA256 (overridable for tests)
ITERATIONS = int(os.environ.get("LOCALFUN_PBKDF2_ITERS", "200000"))
SALT_BYTES = 16
DK_BYTES = 32


def _b64e(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return urlsafe_b64decode(text + pad)


def hash_password(password: str) -> str:
    """Return a portable hash string: pbkdf2_sha256$iter$salt$hash."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=DK_BYTES
    )
    return f"{ALGORITHM}${ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a hash_password() string."""
    if not password or not stored:
        return False
    try:
        algo, iter_s, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != ALGORITHM:
            return False
        iterations = int(iter_s)
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False
