"""
security.py
-----------
Dependency-free password hashing (stdlib scrypt) and opaque session tokens.
Session tokens are random; only their SHA-256 is stored, so a DB leak does not
leak usable sessions.
"""

import base64
import hashlib
import hmac
import os
import secrets
from typing import Tuple

# scrypt cost parameters (~64 MB, ~100 ms). Stored in the hash so they can be raised later.
_N, _R, _P = 2 ** 14, 8, 1
MIN_PASSWORD_LENGTH = 10


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=128 * 1024 * 1024)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
            dklen=len(expected), maxmem=128 * 1024 * 1024,
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def new_session_token() -> Tuple[str, str]:
    """Return (raw_token_for_cookie, sha256_hex_for_db)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
