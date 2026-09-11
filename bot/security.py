"""Security helpers for the public BuildWise runtime.

These controls are deliberately small and auditable. They harden Telegram,
admin, and Pro HTTP boundaries without changing student workflows.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
import hashlib
import logging
import secrets
from time import monotonic
from typing import Final

from fastapi import HTTPException, Request, Response, status


MAX_TELEGRAM_WEBHOOK_BYTES: Final[int] = 512 * 1024

_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}

_SENSITIVE_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "/admin",
    "/internal/",
    "/telegram/",
)
_AUTH_FAILURE_WINDOW_SECONDS: Final[int] = 15 * 60
_PER_CLIENT_AUTH_FAILURE_LIMIT: Final[int] = 8
_GLOBAL_AUTH_FAILURE_LIMIT: Final[int] = 120
_SECURITY_LOG_KEY = secrets.token_bytes(16)
_AUTH_FAILURES: dict[str, deque[float]] = defaultdict(deque)
_AUTH_FAILURE_LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


def _client_fingerprint(request: Request) -> str:
    """Return a privacy-safe stable client identifier for this process.

    Railway documents ``X-Real-IP`` as the edge-provided remote client address.
    Do not trust user-controlled forwarding chains such as ``X-Forwarded-For``
    for an authentication rate-limit key. Outside Railway, fall back to the
    socket peer address exposed by the ASGI server.
    """

    railway_client_ip = request.headers.get("x-real-ip", "").strip()
    raw = railway_client_ip or (request.client.host if request.client else "unknown")
    return hashlib.blake2s(raw.encode("utf-8"), key=_SECURITY_LOG_KEY, digest_size=8).hexdigest()


def _auth_bucket_path(path: str) -> str | None:
    if path.startswith("/admin"):
        return "admin"
    if path == "/pro/login":
        return "pro-login"
    return None


async def _auth_blocked(bucket: str, fingerprint: str) -> bool:
    now = monotonic()
    cutoff = now - _AUTH_FAILURE_WINDOW_SECONDS
    async with _AUTH_FAILURE_LOCK:
        for key in (f"{bucket}:{fingerprint}", f"{bucket}:*"):
            entries = _AUTH_FAILURES[key]
            while entries and entries[0] < cutoff:
                entries.popleft()
        return (
            len(_AUTH_FAILURES[f"{bucket}:{fingerprint}"]) >= _PER_CLIENT_AUTH_FAILURE_LIMIT
            or len(_AUTH_FAILURES[f"{bucket}:*"]) >= _GLOBAL_AUTH_FAILURE_LIMIT
        )


async def _record_auth_failure(bucket: str, fingerprint: str) -> None:
    now = monotonic()
    async with _AUTH_FAILURE_LOCK:
        _AUTH_FAILURES[f"{bucket}:{fingerprint}"].append(now)
        _AUTH_FAILURES[f"{bucket}:*"].append(now)


async def security_guard_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Throttle repeated auth failures and emit privacy-safe security events."""

    path = request.url.path
    fingerprint = _client_fingerprint(request)
    bucket = _auth_bucket_path(path)
    if bucket and await _auth_blocked(bucket, fingerprint):
        logger.warning(
            "security_event event=auth_rate_limited surface=%s client=%s",
            bucket,
            fingerprint,
        )
        return Response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(_AUTH_FAILURE_WINDOW_SECONDS)},
        )

    response = await call_next(request)

    if bucket and response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }:
        await _record_auth_failure(bucket, fingerprint)
        logger.warning(
            "security_event event=auth_failed surface=%s status=%s client=%s",
            bucket,
            response.status_code,
            fingerprint,
        )
    elif path in {"/telegram/webhook", "/internal/reminders"} and response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }:
        logger.warning(
            "security_event event=protected_endpoint_rejected surface=%s status=%s client=%s",
            path,
            response.status_code,
            fingerprint,
        )
    return response


async def add_security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Apply defense-in-depth browser/proxy headers to every HTTP response."""

    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        # Route-specific policies such as the admin dashboard CSP remain stronger
        # and are never overwritten here.
        response.headers.setdefault(name, value)
    if request.url.path.startswith(_SENSITIVE_PATH_PREFIXES):
        response.headers.setdefault("Cache-Control", "no-store, max-age=0")
        response.headers.setdefault("Pragma", "no-cache")
    return response


def validate_json_request(request: Request, *, max_bytes: int) -> None:
    """Reject obviously invalid or oversized JSON requests before parsing them."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="application/json required",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length",
            ) from exc
        if length < 0 or length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large",
            )


async def read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    """Read a request body with a hard byte cap even when Content-Length is absent."""

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large",
            )
    return bytes(body)
