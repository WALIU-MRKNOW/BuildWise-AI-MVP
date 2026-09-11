"""Security helpers for the public BuildWise runtime.

These controls are deliberately small, auditable, and dependency-free. They add
HTTP hardening around the existing Telegram secret-token, admin authentication,
and Supabase service-role boundaries without changing student or Pro workflows.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import HTTPException, Request, Response, status


MAX_TELEGRAM_WEBHOOK_BYTES: Final[int] = 512 * 1024

_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


async def add_security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Apply defense-in-depth browser/proxy headers to every HTTP response."""

    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
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
