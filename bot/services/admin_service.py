"""Server-only admin and support data access for BuildWise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers
from bot.student_access import resolve_student_access


class AdminServiceError(RuntimeError):
    pass


class AdminService:
    """Uses the server secret directly; never instantiate this in browser code."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.supabase_url
        self._key = settings.supabase_secret_key.get_secret_value()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = _auth_headers(self._key)
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=15.0),
        ) as client:
            try:
                response = await client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                raise AdminServiceError("Admin database request failed") from exc
        if response.is_error:
            raise AdminServiceError(f"Admin database request failed ({response.status_code})")
        return response

    async def _rows(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        response = await self._request("GET", path, params=params)
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def _count(self, table: str, filters: dict[str, str] | None = None) -> int:
        params = {"select": "id", "limit": "1", **(filters or {})}
        response = await self._request(
            "GET", f"/rest/v1/{table}", params=params, prefer="count=exact"
        )
        content_range = response.headers.get("content-range", "0-0/0")
        try:
            return int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            return 0

    async def create_support_ticket(
        self,
        *,
        user_id: str,
        telegram_id: int,
        username: str | None,
        display_name: str,
        message: str,
    ) -> str:
        cleaned = message.strip()
        if not 1 <= len(cleaned) <= 4000:
            raise ValueError("Support message must be between 1 and 4000 characters.")
        response = await self._request(
            "POST",
            "/rest/v1/support_tickets",
            json={
                "user_id": user_id,
                "telegram_id": telegram_id,
                "username": username,
                "display_name": display_name or "Student",
                "message": cleaned,
                "status": "open",
            },
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise AdminServiceError("Support ticket could not be created")
        return str(rows[0]["id"])

    async def dashboard(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        seven_days = (now - timedelta(days=7)).isoformat()
        thirty_days = (now - timedelta(days=30)).isoformat()
        today = now.date().isoformat()

        raw_users = await self._rows(
            "/rest/v1/users",
            params={
                "select": "id,telegram_id,username,display_name,student_name,university,onboarding_completed,created_at,updated_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        user_lookup = {str(row["id"]): row for row in raw_users}
        users: list[dict[str, Any]] = []
        for row in raw_users:
            telegram_id = int(row.get("telegram_id") or 0)
            access = resolve_student_access(self._settings, telegram_id, now=now)
            users.append(
                {
                    **row,
                    "access_tier": access.tier.value,
                    "daily_limit": access.daily_limit,
                }
            )

        events = await self._rows(
            "/rest/v1/user_activity_events",
            params={
                "select": "id,user_id,event_type,mode,detail,created_at",
                "order": "created_at.desc",
                "limit": "200",
            },
        )
        recent_activity: list[dict[str, Any]] = []
        for row in events:
            user = user_lookup.get(str(row.get("user_id")), {})
            recent_activity.append(
                {
                    **row,
                    "display_name": user.get("student_name")
                    or user.get("display_name")
                    or "Student",
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "university": user.get("university"),
                }
            )

        mode_event_rows = await self._rows(
            "/rest/v1/user_activity_events",
            params={
                "created_at": f"gte.{thirty_days}",
                "event_type": "eq.ai_request",
                "select": "mode,user_id,created_at",
                "limit": "10000",
            },
        )
        mode_usage: dict[str, int] = {}
        for row in mode_event_rows:
            mode = str(row.get("mode") or "unknown")
            mode_usage[mode] = mode_usage.get(mode, 0) + 1

        # Keep the chart useful immediately after rollout by falling back to
        # conversation history until the new operational event feed has data.
        if not mode_usage:
            historic_modes = await self._rows(
                "/rest/v1/conversations",
                params={
                    "created_at": f"gte.{thirty_days}",
                    "select": "mode,user_id,created_at",
                    "limit": "10000",
                },
            )
            for row in historic_modes:
                mode = str(row.get("mode") or "unknown")
                mode_usage[mode] = mode_usage.get(mode, 0) + 1

        usage_rows = await self._rows(
            "/rest/v1/usage_daily",
            params={
                "usage_date": f"eq.{today}",
                "select": "request_count",
                "limit": "10000",
            },
        )
        tickets = await self._rows(
            "/rest/v1/support_tickets",
            params={
                "select": "id,user_id,telegram_id,username,display_name,message,status,priority,admin_note,created_at,updated_at,resolved_at",
                "order": "created_at.desc",
                "limit": "100",
            },
        )
        active_event_rows = await self._rows(
            "/rest/v1/user_activity_events",
            params={
                "created_at": f"gte.{seven_days}",
                "select": "user_id",
                "limit": "10000",
            },
        )
        active_conversation_rows = await self._rows(
            "/rest/v1/conversations",
            params={
                "created_at": f"gte.{seven_days}",
                "select": "user_id",
                "limit": "10000",
            },
        )
        active_user_ids = {
            str(row.get("user_id"))
            for row in [*active_event_rows, *active_conversation_rows]
            if row.get("user_id")
        }

        return {
            "summary": {
                "users": len(users),
                "active_users_7d": len(active_user_ids),
                "requests_today": sum(
                    int(row.get("request_count") or 0) for row in usage_rows
                ),
                "open_tickets": sum(
                    1 for row in tickets if row.get("status") != "resolved"
                ),
                "onboarded_users": sum(
                    1 for row in users if row.get("onboarding_completed")
                ),
            },
            "users": users,
            "recent_activity": recent_activity,
            "mode_usage": mode_usage,
            "tickets": tickets,
            "generated_at": now.isoformat(),
        }

    async def update_ticket_status(self, ticket_id: str, new_status: str) -> None:
        try:
            UUID(ticket_id)
        except ValueError as exc:
            raise ValueError("Invalid ticket id") from exc
        if new_status not in {"open", "in_progress", "resolved"}:
            raise ValueError("Invalid ticket status")
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {"status": new_status, "updated_at": now}
        payload["resolved_at"] = now if new_status == "resolved" else None
        response = await self._request(
            "PATCH",
            "/rest/v1/support_tickets",
            params={"id": f"eq.{ticket_id}"},
            json=payload,
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise AdminServiceError("Ticket was not found")
