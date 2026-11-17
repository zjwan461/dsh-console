from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

USERNAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,29}[a-z0-9]$")
RESERVED_SUBDOMAINS = {
    "console",
    "api",
    "admin",
    "www",
    "status",
    "mail",
    "caddy",
    "wildcard",
    "dsh",
    "static",
    "assets",
}

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_request_id() -> str:
    """ULID-shaped: 48-bit millisecond timestamp + 80 random bits, Crockford base32."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def validate_username(username: str) -> str | None:
    if not USERNAME_RE.match(username):
        return "用户名需为 3-31 位小写字母/数字/连字符，且以字母开头、字母或数字结尾"
    if username.endswith("-") or "--" in username:
        return "用户名不能以连字符结尾，也不能包含连续连字符"
    if username in RESERVED_SUBDOMAINS:
        return "该用户名为系统保留字"
    return None


def _master_key() -> bytes:
    if settings.master_key:
        raw = base64.urlsafe_b64decode(settings.master_key + "=" * (-len(settings.master_key) % 4))
        if len(raw) != 32:
            raise RuntimeError("MASTER_KEY must be 32 bytes (base64)")
        return raw
    # Dev fallback: deterministic but clearly not for production.
    return hashlib.sha256((settings.session_secret + ":master").encode()).digest()


def encrypt_secret(plaintext: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), b"provider-credential")


def decrypt_secret(ciphertext: bytes) -> str:
    nonce, body = ciphertext[:12], ciphertext[12:]
    return AESGCM(_master_key()).decrypt(nonce, body, b"provider-credential").decode()


def mask_key(key: str) -> str:
    key = key.strip()
    if len(key) <= 8:
        return "****"
    return f"…{key[-4:]}"


def session_expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
