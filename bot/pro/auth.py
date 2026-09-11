"""Preview authentication, CSRF, and persistent usage limiting for BuildWise Pro."""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from datetime import date
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


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
    """Persistent Pro limiter in production, with an isolated in-memory test fallback.

    Production is detected from BUILDWISE_PRO_ENABLED plus the Supabase server
    credentials already required by BuildWise. When Pro is enabled in the process,
    missing database credentials fail closed rather than silently resetting limits.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[str, date], int] = defaultdict(int)
        self._lock = asyncio.Lock()
        enabled = os.getenv("BUILDWISE_PRO_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self._key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
        self._persistent = enabled
        if self._persistent and (not self._base_url or not self._key):
            raise ProAuthError(
                "BuildWise Pro requires Supabase credentials for persistent usage limiting"
            )
        if self._persistent and not self._base_url.startswith("https://"):
            raise ProAuthError("BuildWise Pro usage backend requires HTTPS Supabase URL")

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        if not self._persistent:
            return None
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=_auth_headers(self._key),
            timeout=httpx.Timeout(20.0, connect=10.0),
        ) as client:
            try:
                response = await client.post(f"/rest/v1/rpc/{name}", json=payload)
            except httpx.HTTPError as exc:
                raise ProAuthError("BuildWise Pro usage service is unavailable") from exc
        if response.is_error:
            raise ProAuthError(
                f"BuildWise Pro usage service failed ({response.status_code})"
            )
        return response.json() if response.content else None

    async def reserve(self, subject: str, usage_date: date, limit: int) -> tuple[bool, int]:
        if self._persistent:
            result = await self._rpc(
                "consume_pro_beta_usage",
                {
                    "p_subject": subject,
                    "p_usage_date": usage_date.isoformat(),
                    "p_limit": limit,
                },
            )
            row = result[0] if isinstance(result, list) and result else result
            if not isinstance(row, dict):
                raise ProAuthError("BuildWise Pro usage response was invalid")
            return bool(row.get("allowed")), int(row.get("used") or 0)

        key = (subject, usage_date)
        async with self._lock:
            used = self._counts[key]
            if used >= limit:
                return False, used
            self._counts[key] = used + 1
            return True, used + 1

    async def refund(self, subject: str, usage_date: date) -> int:
        if self._persistent:
            result = await self._rpc(
                "refund_pro_beta_usage",
                {"p_subject": subject, "p_usage_date": usage_date.isoformat()},
            )
            if isinstance(result, list):
                result = result[0] if result else 0
            return int(result or 0)

        key = (subject, usage_date)
        async with self._lock:
            self._counts[key] = max(0, self._counts[key] - 1)
            return self._counts[key]

    async def current(self, subject: str, usage_date: date) -> int:
        if self._persistent:
            result = await self._rpc(
                "get_pro_beta_usage",
                {"p_subject": subject, "p_usage_date": usage_date.isoformat()},
            )
            if isinstance(result, list):
                result = result[0] if result else 0
            return int(result or 0)

        async with self._lock:
            return self._counts[(subject, usage_date)]
