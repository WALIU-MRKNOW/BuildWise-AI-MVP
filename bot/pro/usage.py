"""Persistent daily usage limiting for BuildWise Pro."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


class ProUsageLimiterError(RuntimeError):
    pass


class ProUsageLimiter:
    """Atomic Supabase-backed Pro limiter that survives restarts and replicas."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.supabase_url
        self._key = settings.supabase_secret_key.get_secret_value()

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=_auth_headers(self._key),
            timeout=httpx.Timeout(20.0, connect=10.0),
        ) as client:
            try:
                response = await client.post(f"/rest/v1/rpc/{name}", json=payload)
            except httpx.HTTPError as exc:
                raise ProUsageLimiterError("BuildWise Pro usage service is unavailable") from exc
        if response.is_error:
            raise ProUsageLimiterError(
                f"BuildWise Pro usage service failed ({response.status_code})"
            )
        if not response.content:
            return None
        return response.json()

    async def reserve(self, subject: str, usage_date: date, limit: int) -> tuple[bool, int]:
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
            raise ProUsageLimiterError("BuildWise Pro usage response was invalid")
        return bool(row.get("allowed")), int(row.get("used") or 0)

    async def refund(self, subject: str, usage_date: date) -> int:
        result = await self._rpc(
            "refund_pro_beta_usage",
            {"p_subject": subject, "p_usage_date": usage_date.isoformat()},
        )
        if isinstance(result, list):
            result = result[0] if result else 0
        return int(result or 0)

    async def current(self, subject: str, usage_date: date) -> int:
        result = await self._rpc(
            "get_pro_beta_usage",
            {"p_subject": subject, "p_usage_date": usage_date.isoformat()},
        )
        if isinstance(result, list):
            result = result[0] if result else 0
        return int(result or 0)
