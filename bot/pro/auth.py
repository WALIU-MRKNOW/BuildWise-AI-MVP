"""Preview authentication, CSRF, and in-memory usage limiting for BuildWise Pro."""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from datetime import date
import hashlib
import hmac
import secrets
import time

from bot.config import Settings


SESSION_COOKIE = "buildwise_pro_session"
SESSION_VERSION = "v1"


class ProAuthError(RuntimeError):
    pass


def _secret(value: object | None, name: str, *, min_length: int) -> str:
    if value is None:
        raise ProAuthError(f"{name} is required when BUILDWISE_PRO_WEB_ENABLED=true")
    secret = value.get_secret_value().strip()  # type: ignore[union-attr]
    if len(secret) < min_length:
        raise ProAuthError(f"{name} must contain at least {min_length} characters")
    return secret


def validate_pro_configuration(settings: Settings) -> None:
    if not settings.buildwise_pro_web_enabled:
        return
    if not settings.buildwise_pro_enabled:
        raise ProAuthError(
            "BUILDWISE_PRO_ENABLED must also be true when BUILDWISE_PRO_WEB_ENABLED=true"
        )
    _secret(settings.buildwise_pro_access_key, "BUILDWISE_PRO_ACCESS_KEY", min_length=24)
    _secret(settings.buildwise_pro_session_secret, "BUILDWISE_PRO_SESSION_SECRET", min_length=32)


def access_key_matches(settings: Settings, supplied: str) -> bool:
    expected = _secret(settings.buildwise_pro_access_key, "BUILDWISE_PRO_ACCESS_KEY", min_length=24)
    return bool(supplied) and hmac.compare_digest(expected, supplied.strip())


def _sign(settings: Settings, payload: str) -> str:
    key = _secret(settings.buildwise_pro_session_secret, "BUILDWISE_PRO_SESSION_SECRET", min_length=32)
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_session(settings: Settings, *, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    expires = issued + settings.buildwise_pro_session_ttl_seconds
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("=")
    payload = f"{SESSION_VERSION}.{expires}.{nonce}"
    return f"{payload}.{_sign(settings, payload)}"


def validate_session(settings: Settings, token: str | None, *, now: int | None = None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != SESSION_VERSION:
        return False
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(_sign(settings, payload), parts[3]):
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    return int(time.time() if now is None else now) < expires


def csrf_token(settings: Settings, session: str) -> str:
    return _sign(settings, f"csrf:{session}")


def csrf_matches(settings: Settings, session: str, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(csrf_token(settings, session), supplied)


def stable_subject_hash(settings: Settings) -> str:
    access_key = _secret(settings.buildwise_pro_access_key, "BUILDWISE_PRO_ACCESS_KEY", min_length=24)
    signing_key = _secret(settings.buildwise_pro_session_secret, "BUILDWISE_PRO_SESSION_SECRET", min_length=32)
    return hmac.new(signing_key.encode(), access_key.encode(), hashlib.sha256).hexdigest()


class PreviewUsageLimiter:
    """Process-local preview/beta limiter with exact refund support on failed AI calls."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, date], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def reserve(self, subject: str, usage_date: date, limit: int) -> tuple[bool, int]:
        key = (subject, usage_date)
        async with self._lock:
            used = self._counts[key]
            if used >= limit:
                return False, used
            self._counts[key] = used + 1
            return True, used + 1

    async def refund(self, subject: str, usage_date: date) -> int:
        key = (subject, usage_date)
        async with self._lock:
            self._counts[key] = max(0, self._counts[key] - 1)
            return self._counts[key]

    async def current(self, subject: str, usage_date: date) -> int:
        async with self._lock:
            return self._counts[(subject, usage_date)]
