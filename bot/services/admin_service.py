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


def _student_name(row: dict[str, Any]) -> str:
    return str(row.get("student_name") or row.get("display_name") or "Student")


def _student_identity(row: dict[str, Any]) -> str:
    pieces = [_student_name(row)]
    if row.get("university"):
        pieces.append(str(row["university"]))
    if row.get("username"):
        pieces.append(f"@{row['username']}")
    if row.get("telegram_id") is not None:
        pieces.append(f"ID {row['telegram_id']}")
    return " · ".join(pieces)


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
                "select": "id,telegram_id,username,display_name,student_name,university,onboarding_completed,referral_code,referred_by,created_at,updated_at",
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

        reward_rows = await self._rows(
            "/rest/v1/referral_rewards",
            params={
                "select": "id,referrer_user_id,referred_user_id,credits_awarded,qualified_at,created_at",
                "order": "created_at.desc",
                "limit": "1000",
            },
        )
        balance_rows = await self._rows(
            "/rest/v1/referral_credit_balances",
            params={
                "select": "user_id,available_credits,lifetime_earned,lifetime_spent,updated_at",
                "limit": "10000",
            },
        )
        balance_by_user = {str(row["user_id"]): row for row in balance_rows}
        qualified_counts = Counter(str(row["referrer_user_id"]) for row in reward_rows)

        top_referrers = []
        for user_id, count in referral_counts.most_common(10):
            user = user_by_id.get(user_id, {})
            balance = balance_by_user.get(user_id, {})
            top_referrers.append(
                {
                    "user_id": user_id,
                    "display_name": _student_name(user),
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "referral_code": user.get("referral_code"),
                    "referral_count": count,
                    "qualified_referrals": qualified_counts.get(user_id, 0),
                    "available_credits": int(balance.get("available_credits") or 0),
                    "lifetime_earned": int(balance.get("lifetime_earned") or 0),
                }
            )

        # The admin dashboard receives the full current user list (up to the
        # server-side safety cap), with every Telegram numeric ID visible even
        # when a username exists.
        users = []
        for row in referral_users:
            user_id = str(row["id"])
            balance = balance_by_user.get(user_id, {})
            raw_username = str(row.get("username") or "").strip()
            telegram_id = row.get("telegram_id")
            telegram_label = (
                f"{raw_username} · ID {telegram_id}"
                if raw_username
                else None
            )
            display = _student_name(row)
            if row.get("university"):
                display = f"{display} · {row['university']}"
            users.append(
                {
                    **row,
                    "display_name": display,
                    "username": telegram_label,
                    "referral_count": referral_counts.get(user_id, 0),
                    "qualified_referrals": qualified_counts.get(user_id, 0),
                    "available_credits": int(balance.get("available_credits") or 0),
                }
            )

        recent_rewards = []
        for row in reward_rows[:50]:
            referrer = user_by_id.get(str(row.get("referrer_user_id")), {})
            referred = user_by_id.get(str(row.get("referred_user_id")), {})
            recent_rewards.append(
                {
                    **row,
                    "referrer_name": _student_name(referrer),
                    "referrer_username": referrer.get("username"),
                    "referred_name": _student_name(referred),
                    "referred_username": referred.get("username"),
                }
            )

        recent_activity = await self._rows(
            "/rest/v1/conversations",
            params={
                "select": "id,user_id,mode,category,message,created_at",
                "order": "created_at.desc",
                "limit": "100",
            },
        )
        user_lookup: dict[str, dict[str, Any]] = {
            str(row["id"]): row for row in referral_users
        }

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
                    "display_name": _student_identity(user),
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "university": user.get("university"),
                    "message": str(row.get("message") or "")[:500],
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
                "qualified_referrals": len(reward_rows),
                "reward_credits_issued": sum(
                    int(row.get("credits_awarded") or 0) for row in reward_rows
                ),
                "reward_credits_available": sum(
                    int(row.get("available_credits") or 0) for row in balance_rows
                ),
            },
            "users": users,
            "recent_activity": activity,
            "mode_usage": mode_usage,
            "tickets": tickets,
            "top_referrers": top_referrers,
            "recent_rewards": recent_rewards,
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
