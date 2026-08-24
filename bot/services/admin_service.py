"""Server-only admin and support data access for BuildWise."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


class AdminServiceError(RuntimeError):
    pass


class AdminService:
    """Uses the server secret directly; never instantiate this in browser code."""

    def __init__(self, settings: Settings) -> None:
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

        referral_users = await self._rows(
            "/rest/v1/users",
            params={
                "select": "id,telegram_id,username,display_name,referral_code,referred_by,created_at,updated_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        referral_counts = Counter(
            str(row["referred_by"])
            for row in referral_users
            if row.get("referred_by")
        )
        user_by_id = {str(row["id"]): row for row in referral_users}
        top_referrers = []
        for user_id, count in referral_counts.most_common(10):
            user = user_by_id.get(user_id, {})
            top_referrers.append(
                {
                    "user_id": user_id,
                    "display_name": user.get("display_name") or "Student",
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "referral_code": user.get("referral_code"),
                    "referral_count": count,
                }
            )

        users = [
            {**row, "referral_count": referral_counts.get(str(row["id"]), 0)}
            for row in referral_users[:30]
        ]
        recent_activity = await self._rows(
            "/rest/v1/conversations",
            params={
                "select": "id,user_id,mode,category,message,created_at",
                "order": "created_at.desc",
                "limit": "30",
            },
        )
        recent_user_ids = sorted(
            {str(row["user_id"]) for row in recent_activity if row.get("user_id")}
        )
        user_lookup: dict[str, dict[str, Any]] = {
            str(row["id"]): row for row in referral_users
        }
        if recent_user_ids:
            missing_ids = [user_id for user_id in recent_user_ids if user_id not in user_lookup]
            if missing_ids:
                ids = ",".join(missing_ids)
                activity_users = await self._rows(
                    "/rest/v1/users",
                    params={
                        "id": f"in.({ids})",
                        "select": "id,telegram_id,username,display_name",
                        "limit": "100",
                    },
                )
                user_lookup.update({str(row["id"]): row for row in activity_users})

        mode_rows = await self._rows(
            "/rest/v1/conversations",
            params={
                "created_at": f"gte.{thirty_days}",
                "select": "mode,user_id,created_at",
                "limit": "10000",
            },
        )
        mode_usage: dict[str, int] = {}
        for row in mode_rows:
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
        active_rows = await self._rows(
            "/rest/v1/conversations",
            params={
                "created_at": f"gte.{seven_days}",
                "select": "user_id",
                "limit": "10000",
            },
        )

        activity = []
        for row in recent_activity:
            user = user_lookup.get(str(row.get("user_id")), {})
            activity.append(
                {
                    **row,
                    "display_name": user.get("display_name") or "Student",
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "message": str(row.get("message") or "")[:240],
                }
            )

        return {
            "summary": {
                "users": len(referral_users),
                "active_users_7d": len(
                    {str(row.get("user_id")) for row in active_rows if row.get("user_id")}
                ),
                "requests_today": sum(
                    int(row.get("request_count") or 0) for row in usage_rows
                ),
                "conversations_30d": len(mode_rows),
                "open_tickets": sum(
                    1 for row in tickets if row.get("status") != "resolved"
                ),
                "referrals": sum(referral_counts.values()),
            },
            "users": users,
            "recent_activity": activity,
            "mode_usage": mode_usage,
            "tickets": tickets,
            "top_referrers": top_referrers,
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
