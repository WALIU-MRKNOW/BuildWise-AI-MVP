"""Small profile store built on the existing private saved_items table.

This avoids adding a new database table while keeping onboarding metadata out of
student answer history at the handler layer.
"""

from __future__ import annotations

from typing import Any

from bot.services.supabase_service import SupabaseService

PROFILE_ITEM_TYPE = "student_profile"
PROFILE_NAME = "preferred_name"
PROFILE_UNIVERSITY = "university"
PROFILE_ADMIN_NOTIFIED = "admin_notified"


class StudentProfileStore:
    def __init__(self, database: SupabaseService) -> None:
        self._database = database

    async def values(self, user_id: str) -> dict[str, str]:
        rows = await self._database._request(  # server-only service wrapper
            "GET",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"eq.{PROFILE_ITEM_TYPE}",
                "select": "title,content,created_at",
                "order": "created_at.desc",
                "limit": "20",
            },
        )
        values: dict[str, str] = {}
        for row in rows or []:
            key = str(row.get("title") or "").strip()
            if key and key not in values:
                values[key] = str(row.get("content") or "").strip()
        return values

    async def set_value(self, user_id: str, key: str, value: str) -> None:
        cleaned = " ".join(value.split()).strip()
        await self._database._request(
            "POST",
            "/rest/v1/saved_items",
            json={
                "user_id": user_id,
                "item_type": PROFILE_ITEM_TYPE,
                "title": key[:160],
                "content": cleaned[:1000],
                "file_reference": None,
            },
            headers={"Prefer": "return=minimal"},
        )

    async def roster(self, *, limit: int = 500) -> list[dict[str, Any]]:
        users = await self._database._request(
            "GET",
            "/rest/v1/users",
            params={
                "select": "id,telegram_id,username,display_name,created_at,updated_at",
                "order": "created_at.desc",
                "limit": str(max(1, min(limit, 5000))),
            },
        )
        profile_rows = await self._database._request(
            "GET",
            "/rest/v1/saved_items",
            params={
                "item_type": f"eq.{PROFILE_ITEM_TYPE}",
                "select": "user_id,title,content,created_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        metadata: dict[str, dict[str, str]] = {}
        for row in profile_rows or []:
            user_id = str(row.get("user_id") or "")
            key = str(row.get("title") or "")
            if not user_id or not key:
                continue
            metadata.setdefault(user_id, {})
            if key not in metadata[user_id]:
                metadata[user_id][key] = str(row.get("content") or "")

        result: list[dict[str, Any]] = []
        for row in users or []:
            user_id = str(row.get("id"))
            meta = metadata.get(user_id, {})
            result.append(
                {
                    **row,
                    "preferred_name": meta.get(PROFILE_NAME) or row.get("display_name") or "Student",
                    "university": meta.get(PROFILE_UNIVERSITY) or "Not provided",
                }
            )
        return result

    async def recent_activity(self, *, limit: int = 40) -> list[dict[str, Any]]:
        rows = await self._database._request(
            "GET",
            "/rest/v1/conversations",
            params={
                "select": "id,user_id,mode,category,message,created_at",
                "order": "created_at.desc",
                "limit": str(max(1, min(limit, 200))),
            },
        )
        roster = await self.roster(limit=5000)
        by_id = {str(row.get("id")): row for row in roster}
        activity: list[dict[str, Any]] = []
        for row in rows or []:
            user = by_id.get(str(row.get("user_id")), {})
            activity.append(
                {
                    **row,
                    "preferred_name": user.get("preferred_name") or user.get("display_name") or "Student",
                    "telegram_id": user.get("telegram_id"),
                    "username": user.get("username"),
                    "university": user.get("university") or "Not provided",
                    "message": str(row.get("message") or "")[:500],
                }
            )
        return activity
