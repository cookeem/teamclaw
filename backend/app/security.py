from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
from typing import Any

import jwt

JWT_ALGORITHM = "HS256"
DEFAULT_JWT_SECRET = "teamclaw-dev-secret-change-me"
PASSWORD_POLICY_ERROR = "Password must be 8-256 chars and include uppercase, lowercase, number, and special character."
_PASSWORD_SPECIAL_PATTERN = re.compile(r"[^A-Za-z0-9\s]")


def _jwt_secret() -> str:
    raw = os.environ.get("TEAMCLAW_JWT_SECRET")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_JWT_SECRET


def access_token_ttl_seconds() -> int:
    raw = os.environ.get("TEAMCLAW_ACCESS_TOKEN_TTL_SECONDS", "7200")
    try:
        parsed = int(raw)
    except ValueError:
        return 7200
    return parsed if parsed > 0 else 7200


def refresh_token_ttl_days() -> int:
    raw = os.environ.get("TEAMCLAW_REFRESH_TOKEN_TTL_DAYS", "30")
    try:
        parsed = int(raw)
    except ValueError:
        return 30
    return parsed if parsed > 0 else 30


def create_access_token(*, user_id: str, username: str, is_admin: bool) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(seconds=access_token_ttl_seconds())
    payload = {
        "sub": user_id,
        "username": username,
        "is_admin": is_admin,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "access":
        return None
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_expires_at() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=refresh_token_ttl_days())


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def validate_password_policy(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8 or len(password) > 256:
        raise ValueError(PASSWORD_POLICY_ERROR)
    if not any(ch.islower() for ch in password):
        raise ValueError(PASSWORD_POLICY_ERROR)
    if not any(ch.isupper() for ch in password):
        raise ValueError(PASSWORD_POLICY_ERROR)
    if not any(ch.isdigit() for ch in password):
        raise ValueError(PASSWORD_POLICY_ERROR)
    if _PASSWORD_SPECIAL_PATTERN.search(password) is None:
        raise ValueError(PASSWORD_POLICY_ERROR)


def hash_password(password: str) -> str:
    validate_password_policy(password)
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    key = hashlib.scrypt(
        password=password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=64,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    key_b64 = base64.b64encode(key).decode("ascii")
    return f"scrypt${n}${r}${p}${salt_b64}${key_b64}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, key_b64 = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        n = int(n_raw)
        r = int(r_raw)
        p = int(p_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(key_b64.encode("ascii"))
    except Exception:  # noqa: BLE001
        return False

    derived = hashlib.scrypt(
        password=password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
    )
    return hmac.compare_digest(derived, expected)
