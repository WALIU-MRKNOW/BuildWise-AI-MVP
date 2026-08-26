"""Preview authentication and CSRF helpers for the BuildWise WebMCP surface."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from bot.config import Settings

SESSION_COOKIE = "buildwise_webmcp_session"
SESSION_VERSION = "v1"


class WebMCPAuthError(RuntimeError):
    pass


def _secret_value(value: object | None, name: str, *, min_length: int = 24) -> str:
    if value is None:
        raise WebMCPAuthError(f"{name} is required when WEBMCP_ENABLED=true")
    secret = value.get_secret_value().strip()  # type: ignore[union-attr]
    if len(secret) < min_length:
        raise WebMCPAuthError(f"{name} must contain at least {min_length} characters")
    return secret


def validate_webmcp_configuration(settings: Settings) -> None:
    if not settings.webmcp_enabled:
        return
    _secret_value(settings.webmcp_access_key, "WEBMCP_ACCESS_KEY")
    _secret_value(settings.webmcp_session_secret, "WEBMCP_SESSION_SECRET", min_length=32)


def access_key_matches(settings: Settings, supplied: str) -> bool:
    expected = _secret_value(settings.webmcp_access_key, "WEBMCP_ACCESS_KEY")
    return bool(supplied) and hmac.compare_digest(expected, supplied.strip())


def _sign(settings: Settings, message: str) -> str:
    secret = _secret_value(settings.webmcp_session_secret, "WEBMCP_SESSION_SECRET", min_length=32)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session(settings: Settings, *, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    expires = issued + settings.webmcp_session_ttl_seconds
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode("ascii").rstrip("=")
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
    current = int(time.time() if now is None else now)
    return current < expires


def csrf_token(settings: Settings, session_token: str) -> str:
    return _sign(settings, f"csrf:{session_token}")


def csrf_matches(settings: Settings, session_token: str, supplied: str | None) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(csrf_token(settings, session_token), supplied)


def subject_hash(settings: Settings) -> str:
    """Stable, non-reversible identity for the shared preview account."""
    return _sign(settings, "subject:webmcp-preview-user")
