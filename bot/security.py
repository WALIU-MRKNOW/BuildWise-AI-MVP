"""Security helpers for the public BuildWise runtime.

These controls are deliberately small and auditable. They harden the public HTTP
surface without logging credentials, Telegram payloads, document contents, or
other student data.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
import logging
from threading import Lock
from time import monotonic
from typing import Final

from fastapi import HTTPException, Request, Response, status


logger = logging.getLogger("buildwise.security")
MAX_TELEGRAM_WEBHOOK_BYTES: Final[int] = 512 * 1024
AUTH_FAILURE_WINDOW_SECONDS: Final[float] = 60.0
AUTH_FAILURE_LIMIT: Final[int] = 30

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
    "/pro",
)
_SECURITY_EVENT_STATUSES: Final[frozenset[int]] = frozenset({401, 403, 413, 429})
_auth_failures: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)
_auth_failure_lock = Lock()


def _security_path(path: str) -> str:
    """Return a low-cardinality path label without IDs or query values."""
    if path.startswith("/admin/api/tickets/"):
        return "/admin/api/tickets/:id"
    if path.startswith("/admin"):
        return "/admin"
    if path.startswith("/telegram/"):
        return "/telegram"
    if path.startswith("/internal/"):
        return "/internal"
    if path.startswith("/pro"):
        return "/pro"
    return "/other"


def _auth_surface(path: str) -> str | None:
    if path.startswith("/admin"):
        return "/admin"
    if path == "/pro/login" or path.startswith("/pro/report"):
        return "/pro"
    return None


def _client_key(request: Request) -> str:
    # Uvicorn/Railway supplies request.client after proxy handling. Do not persist
    # or log this value; it is used only as an in-memory abuse-control key.
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host[:128]


def _too_many_auth_failures(request: Request, surface: str) -> bool:
    key = (_client_key(request), surface)
    now = monotonic()
    cutoff = now - AUTH_FAILURE_WINDOW_SECONDS
    with _auth_failure_lock:
        failures = _auth_failures[key]
        while failures and failures[0] < cutoff:
            failures.popleft()
        return len(failures) >= AUTH_FAILURE_LIMIT


def _record_auth_result(request: Request, surface: str, status_code: int) -> None:
    key = (_client_key(request), surface)
    now = monotonic()
    cutoff = now - AUTH_FAILURE_WINDOW_SECONDS
    with _auth_failure_lock:
        failures = _auth_failures[key]
        while failures and failures[0] < cutoff:
            failures.popleft()
        if status_code in {401, 403}:
            failures.append(now)
        elif status_code < 400:
            # A valid authenticated request clears stale failures for that client.
            failures.clear()
        if not failures:
            _auth_failures.pop(key, None)


async def log_security_events(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Rate-limit repeated auth failures and log security events without secrets."""
    surface = _auth_surface(request.url.path)
    if surface is not None and _too_many_auth_failures(request, surface):
        logger.warning(
            "security_event method=%s path=%s status=429 reason=auth_rate_limit",
            request.method,
            surface,
        )
        return Response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(int(AUTH_FAILURE_WINDOW_SECONDS)),
                "Cache-Control": "no-store, max-age=0",
            },
        )

    response = await call_next(request)
    if surface is not None:
        _record_auth_result(request, surface, response.status_code)
    if response.status_code in _SECURITY_EVENT_STATUSES:
        logger.warning(
            "security_event method=%s path=%s status=%s",
            request.method,
            _security_path(request.url.path),
            response.status_code,
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
