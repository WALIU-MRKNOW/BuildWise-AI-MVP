"""Server-only referral tracking for BuildWise Telegram users."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers

_REFERRAL_CODE_RE = re.compile(r"^[a-f0-9]{32}$")


class ReferralServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReferralRegistration:
    user_id: str
    referral_code: str
    referred_by: str | None
    attributed: bool
    was_new: bool


def normalize_referral_code(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned if _REFERRAL_CODE_RE.fullmatch(cleaned) else None


def build_referral_link(bot_username: str, referral_code: str) -> str:
    username = bot_username.strip().lstrip("@")
    code = normalize_referral_code(referral_code)
    if not username or code is None:
        raise ValueError("Invalid referral link data")
    return f"https://t.me/{username}?start={code}"


class ReferralService:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.supabase_url
        self._key = settings.supabase_secret_key.get_secret_value()

    async def _rpc(self, function: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=_auth_headers(self._key),
            timeout=httpx.Timeout(30.0, connect=15.0),
        ) as client:
            try:
                response = await client.post(f"/rest/v1/rpc/{function}", json=payload)
            except httpx.HTTPError as exc:
                raise ReferralServiceError("Referral database request failed") from exc
        if response.is_error:
            raise ReferralServiceError(
                f"Referral database request failed ({response.status_code})"
            )
        return response.json() if response.content else None

    async def register_start(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str,
        referral_code: str | None,
    ) -> ReferralRegistration:
        rows = await self._rpc(
            "register_referral_start",
            {
                "p_telegram_id": telegram_id,
                "p_username": username,
                "p_display_name": display_name,
                "p_referral_code": normalize_referral_code(referral_code),
            },
        )
        if not rows:
            raise ReferralServiceError("Referral registration returned no user")
        row = rows[0] if isinstance(rows, list) else rows
        return ReferralRegistration(
            user_id=str(row["user_id"]),
            referral_code=str(row["referral_code"]),
            referred_by=str(row["referred_by"]) if row.get("referred_by") else None,
            attributed=bool(row.get("attributed", False)),
            was_new=bool(row.get("was_new", False)),
        )

    async def referral_count(self, user_id: str) -> int:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=_auth_headers(self._key),
            timeout=httpx.Timeout(30.0, connect=15.0),
        ) as client:
            try:
                response = await client.get(
                    "/rest/v1/users",
                    params={
                        "referred_by": f"eq.{user_id}",
                        "select": "id",
                        "limit": "1",
                    },
                    headers={"Prefer": "count=exact"},
                )
            except httpx.HTTPError as exc:
                raise ReferralServiceError("Referral count request failed") from exc
        if response.is_error:
            raise ReferralServiceError(
                f"Referral count request failed ({response.status_code})"
            )
        content_range = response.headers.get("content-range", "0-0/0")
        try:
            return int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            return 0
