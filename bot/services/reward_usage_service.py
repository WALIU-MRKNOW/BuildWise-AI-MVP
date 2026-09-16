"""Student access tiers and onboarding profile persistence.

The retired referral/reward system intentionally does not participate in usage
accounting. Daily usage is enforced by the atomic BuildWise database RPC, with
higher configured limits only for explicit founder and pilot Telegram IDs.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from bot.domain import UsageStatus, UserProfile
from bot.services.supabase_service import SupabaseError, SupabaseService

_STUDENT_PROFILE_ITEM_TYPE = "student_profile"
_STUDENT_PROFILE_TITLE = "BuildWise student profile"
_HISTORY_PAGE_SIZE = 500
logger = logging.getLogger(__name__)


def _parse_telegram_ids(raw: str, variable: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    values: set[int] = set()
    for raw_value in raw.split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{variable} must contain numeric Telegram IDs") from exc
        if parsed <= 0:
            raise ValueError(f"{variable} must contain positive Telegram IDs")
        values.add(parsed)
    return frozenset(values)


def _configured_daily_limit(variable: str, default: int, normal_limit: int) -> int:
    raw = os.getenv(variable, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{variable} must be an integer") from exc
    if value < 1 or value > 10000:
        raise ValueError(f"{variable} must be between 1 and 10000")
    return max(normal_limit, value)


def founder_telegram_ids() -> frozenset[int]:
    return _parse_telegram_ids(os.getenv("FOUNDER_TELEGRAM_IDS", ""), "FOUNDER_TELEGRAM_IDS")


def pilot_telegram_ids() -> frozenset[int]:
    return _parse_telegram_ids(os.getenv("PILOT_TELEGRAM_IDS", ""), "PILOT_TELEGRAM_IDS")


def founder_daily_limit(normal_limit: int) -> int:
    return _configured_daily_limit("FOUNDER_DAILY_AI_REQUEST_LIMIT", 1000, normal_limit)


def pilot_daily_limit(normal_limit: int) -> int:
    return _configured_daily_limit("PILOT_DAILY_AI_REQUEST_LIMIT", 40, normal_limit)


class StudentAccessSupabaseService(SupabaseService):
    """Apply explicit student access tiers while preserving onboarding identity."""

    async def consume_usage(self, profile: UserProfile, limit: int) -> UsageStatus:
        if profile.telegram_id in founder_telegram_ids():
            effective_limit = founder_daily_limit(limit)
        elif profile.telegram_id in pilot_telegram_ids():
            effective_limit = pilot_daily_limit(limit)
        else:
            effective_limit = limit
        return await super().consume_usage(profile, effective_limit)

    async def get_student_identity(self, user_id: str) -> dict[str, str] | None:
        rows = await self._request(
            "GET",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"eq.{_STUDENT_PROFILE_ITEM_TYPE}",
                "select": "content",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        try:
            payload = json.loads(str(rows[0].get("content") or "{}"))
        except json.JSONDecodeError:
            return None
        name = str(payload.get("name") or "").strip()
        university = str(payload.get("university") or "").strip()
        if not name or not university:
            return None
        return {"name": name, "university": university}

    async def save_student_identity(self, user_id: str, *, name: str, university: str) -> None:
        """Create the replacement profile before removing the old one.

        If the insert fails, the previous onboarding identity remains intact. If the
        best-effort cleanup fails, newest-first reads still select the new profile.
        """
        payload = json.dumps(
            {"name": name.strip(), "university": university.strip()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        rows = await self._request(
            "POST",
            "/rest/v1/saved_items",
            json={
                "user_id": user_id,
                "item_type": _STUDENT_PROFILE_ITEM_TYPE,
                "title": _STUDENT_PROFILE_TITLE,
                "content": payload,
                "file_reference": None,
            },
            headers={"Prefer": "return=representation"},
        )
        if not rows or not rows[0].get("id"):
            raise SupabaseError("Student identity could not be saved")
        new_id = str(rows[0]["id"])
        try:
            await self._request(
                "DELETE",
                "/rest/v1/saved_items",
                params={
                    "user_id": f"eq.{user_id}",
                    "item_type": f"eq.{_STUDENT_PROFILE_ITEM_TYPE}",
                    "id": f"neq.{new_id}",
                },
            )
        except SupabaseError:
            logger.warning("Could not prune an older student identity row user_id=%s", user_id)

    async def recent_saved_items(self, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"neq.{_STUDENT_PROFILE_ITEM_TYPE}",
                "select": "id,item_type,title,content,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def attach_file_to_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
        file_reference: str,
    ) -> None:
        """Attach a successfully persisted upload to exactly one owned conversation."""
        rows = await self._request(
            "PATCH",
            "/rest/v1/conversations",
            params={
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
            },
            json={"file_reference": file_reference},
            headers={"Prefer": "return=representation"},
        )
        if not rows:
            raise SupabaseError("Conversation attachment could not be linked")

    async def _paged_history_rows(
        self,
        table: str,
        user_id: str,
        *,
        exclude_student_profile: bool = False,
    ) -> list[dict[str, Any]]:
        """Read every history row in bounded pages instead of silently truncating at 500."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {
                "user_id": f"eq.{user_id}",
                "select": "id,file_reference",
                "order": "created_at.asc",
                "limit": str(_HISTORY_PAGE_SIZE),
                "offset": str(offset),
            }
            if exclude_student_profile:
                params["item_type"] = f"neq.{_STUDENT_PROFILE_ITEM_TYPE}"
            page = await self._request("GET", f"/rest/v1/{table}", params=params)
            if not isinstance(page, list) or not page:
                break
            rows.extend(page)
            if len(page) < _HISTORY_PAGE_SIZE:
                break
            offset += len(page)
        return rows

    async def delete_history(self, user_id: str) -> int:
        """Delete all student work/files while preserving required onboarding identity."""
        conversations = await self._paged_history_rows("conversations", user_id)
        saved_items = await self._paged_history_rows(
            "saved_items", user_id, exclude_student_profile=True
        )
        references = {
            str(row["file_reference"])
            for row in (*conversations, *saved_items)
            if row.get("file_reference")
        }
        for reference in references:
            try:
                await self.delete_file(reference)
            except SupabaseError:
                logger.warning("Could not remove stored history file user_id=%s", user_id)
        await self._request(
            "DELETE", "/rest/v1/conversations", params={"user_id": f"eq.{user_id}"}
        )
        await self._request(
            "DELETE",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"neq.{_STUDENT_PROFILE_ITEM_TYPE}",
            },
        )
        return len(conversations)


# Temporary import compatibility for any internal tests/tools that still reference
# the old class name. It no longer provides referral rewards or referral credits.
RewardAwareSupabaseService = StudentAccessSupabaseService
