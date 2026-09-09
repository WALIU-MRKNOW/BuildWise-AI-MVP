"""Server-only admin and support data access for BuildWise."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any
from uuid import UUID

import httpx

from bot.config import Settings
from bot.services.supabase_service import _auth_headers


class AdminServiceError(RuntimeError):
    pass


def _parse_ids(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError:
            continue
        if parsed > 0:
            values.add(parsed)
    return frozenset(values)


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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

    @staticmethod
    def _profile_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        profiles: dict[str, dict[str, str]] = {}
        for row in rows:
            user_id = str(row.get("user_id") or "")
            if not user_id or user_id in profiles:
                continue
            try:
                payload = json.loads(str(row.get("content") or "{}"))
            except json.JSONDecodeError:
                continue
            name = str(payload.get("name") or "").strip()
            university = str(payload.get("university") or "").strip()
            if name or university:
                profiles[user_id] = {"name": name, "university": university}
        return profiles

    @staticmethod
    def _tier_map(rows: list[dict[str, Any]]) -> dict[str, datetime]:
        now = datetime.now(timezone.utc)
        tiers: dict[str, datetime] = {}
        for row in rows:
            user_id = str(row.get("user_id") or "")
            if not user_id or user_id in tiers:
                continue
            try:
                payload = json.loads(str(row.get("content") or "{}"))
            except json.JSONDecodeError:
                continue
            if str(payload.get("tier") or "").lower() != "pilot":
                continue
            expires_at = _parse_timestamp(payload.get("expires_at"))
            if expires_at is not None and expires_at > now:
                tiers[user_id] = expires_at
        return tiers

    @staticmethod
    def _effective_tier(
        *, telegram_id: int, user_id: str, database_pilots: dict[str, datetime]
    ) -> tuple[str, datetime | None]:
        founders = _parse_ids(os.getenv("FOUNDER_TELEGRAM_IDS", ""))
        configured_pilots = _parse_ids(os.getenv("PILOT_TELEGRAM_IDS", ""))
        if telegram_id in founders:
            return "Founder", None
        if telegram_id in configured_pilots:
            return "Pilot", None
        expires_at = database_pilots.get(user_id)
        if expires_at is not None:
            return "Pilot", expires_at
        return "Normal", None

    async def _student_metadata(
        self,
    ) -> tuple[dict[str, dict[str, str]], dict[str, datetime]]:
        profile_rows = await self._rows(
            "/rest/v1/saved_items",
            params={
                "item_type": "eq.student_profile",
                "select": "user_id,content,created_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        tier_rows = await self._rows(
            "/rest/v1/saved_items",
            params={
                "item_type": "eq.usage_tier",
                "select": "user_id,content,created_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        return self._profile_map(profile_rows), self._tier_map(tier_rows)

    @staticmethod
    def _enrich_user(
        row: dict[str, Any],
        profiles: dict[str, dict[str, str]],
        tiers: dict[str, datetime],
    ) -> dict[str, Any]:
        user_id = str(row.get("id") or "")
        identity = profiles.get(user_id, {})
        name = identity.get("name") or str(row.get("display_name") or "Student")
        university = identity.get("university") or "Institution not added"
        telegram_id = int(row.get("telegram_id") or 0)
        username = str(row.get("username") or "").strip()
        tier, pilot_until = AdminService._effective_tier(
            telegram_id=telegram_id,
            user_id=user_id,
            database_pilots=tiers,
        )
        return {
            **row,
            "name": name,
            "display_name": name,
            "university": university,
            "telegram_id": telegram_id,
            "username": username or None,
            "tier": tier,
            "pilot_until": pilot_until.isoformat() if pilot_until else None,
        }

    async def list_students(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 100)
        rows = await self._rows(
            "/rest/v1/users",
            params={
                "select": "id,telegram_id,username,display_name,created_at,updated_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        profiles, tiers = await self._student_metadata()
        return [self._enrich_user(row, profiles, tiers) for row in rows]

    async def student_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        rows = await self._rows(
            "/rest/v1/users",
            params={
                "telegram_id": f"eq.{int(telegram_id)}",
                "select": "id,telegram_id,username,display_name,created_at,updated_at",
                "limit": "1",
            },
        )
        if not rows:
            return None
        profiles, tiers = await self._student_metadata()
        return self._enrich_user(rows[0], profiles, tiers)

    async def recent_admin_activity(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 50)
        activity = await self._rows(
            "/rest/v1/conversations",
            params={
                "select": "id,user_id,mode,category,message,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        user_ids = {str(row.get("user_id") or "") for row in activity if row.get("user_id")}
        if not user_ids:
            return []
        users = await self._rows(
            "/rest/v1/users",
            params={
                "id": f"in.({','.join(user_ids)})",
                "select": "id,telegram_id,username,display_name,created_at,updated_at",
                "limit": str(max(1, len(user_ids))),
            },
        )
        profiles, tiers = await self._student_metadata()
        lookup = {
            str(row["id"]): self._enrich_user(row, profiles, tiers) for row in users
        }
        result: list[dict[str, Any]] = []
        for row in activity:
            user = lookup.get(str(row.get("user_id") or ""), {})
            result.append(
                {
                    **row,
                    "name": user.get("name") or "Student",
                    "university": user.get("university") or "Institution not added",
                    "telegram_id": user.get("telegram_id"),
                    "username": user.get("username"),
                    "tier": user.get("tier") or "Normal",
                    "message": str(row.get("message") or "")[:500],
                }
            )
        return result

    async def set_pilot(self, telegram_id: int, *, days: int = 14) -> datetime:
        if days < 1 or days > 90:
            raise ValueError("Pilot duration must be between 1 and 90 days")
        student = await self.student_by_telegram_id(telegram_id)
        if student is None:
            raise ValueError("Telegram ID is not a BuildWise student")
        user_id = str(student["id"])
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        await self._request(
            "DELETE",
            "/rest/v1/saved_items",
            params={"user_id": f"eq.{user_id}", "item_type": "eq.usage_tier"},
        )
        payload = json.dumps(
            {"tier": "pilot", "expires_at": expires_at.isoformat()},
            separators=(",", ":"),
        )
        await self._request(
            "POST",
            "/rest/v1/saved_items",
            json={
                "user_id": user_id,
                "item_type": "usage_tier",
                "title": "BuildWise usage tier",
                "content": payload,
                "file_reference": None,
            },
            prefer="return=minimal",
        )
        return expires_at

    async def set_normal(self, telegram_id: int) -> None:
        student = await self.student_by_telegram_id(telegram_id)
        if student is None:
            raise ValueError("Telegram ID is not a BuildWise student")
        await self._request(
            "DELETE",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{student['id']}",
                "item_type": "eq.usage_tier",
            },
        )

    async def dashboard(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        seven_days = (now - timedelta(days=7)).isoformat()
        thirty_days = (now - timedelta(days=30)).isoformat()
        today = now.date().isoformat()

        user_rows = await self._rows(
            "/rest/v1/users",
            params={
                "select": "id,telegram_id,username,display_name,created_at,updated_at",
                "order": "created_at.desc",
                "limit": "10000",
            },
        )
        profiles, tiers = await self._student_metadata()
        user_lookup = {str(row["id"]): row for row in user_rows}

        recent_activity = await self._rows(
            "/rest/v1/conversations",
            params={
                "select": "id,user_id,mode,category,message,created_at",
                "order": "created_at.desc",
                "limit": "100",
            },
        )
        mode_rows = await self._rows(
            "/rest/v1/conversations",
            params={
                "created_at": f"gte.{thirty_days}",
                "select": "mode,user_id,created_at",
                "limit": "10000",
            },
        )
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

        mode_usage: dict[str, int] = {}
        for row in mode_rows:
            mode = str(row.get("mode") or "unknown")
            mode_usage[mode] = mode_usage.get(mode, 0) + 1

        users: list[dict[str, Any]] = []
        for row in user_rows:
            user = self._enrich_user(row, profiles, tiers)
            tier_note = (
                f"Pilot until {user['pilot_until'][:10]}"
                if user.get("pilot_until")
                else str(user["tier"])
            )
            users.append(
                {
                    **user,
                    "display_name": f"{user['name']} · {user['university']} · {tier_note}",
                    "username": (
                        f"{user['username']} · ID {user['telegram_id']}"
                        if user.get("username")
                        else None
                    ),
                }
            )

        activity: list[dict[str, Any]] = []
        for row in recent_activity:
            user_id = str(row.get("user_id") or "")
            raw_user = user_lookup.get(user_id, {})
            user = self._enrich_user(raw_user, profiles, tiers) if raw_user else {}
            identity_bits = [str(user.get("name") or "Student")]
            if user.get("username"):
                identity_bits.append(f"@{user['username']}")
            if user.get("telegram_id"):
                identity_bits.append(f"ID {user['telegram_id']}")
            if user.get("university"):
                identity_bits.append(str(user["university"]))
            identity_bits.append(str(user.get("tier") or "Normal"))
            activity.append(
                {
                    **row,
                    "display_name": " · ".join(identity_bits),
                    "username": user.get("username"),
                    "telegram_id": user.get("telegram_id"),
                    "university": user.get("university") or "Institution not added",
                    "tier": user.get("tier") or "Normal",
                    "message": str(row.get("message") or "")[:500],
                }
            )

        enriched_tickets: list[dict[str, Any]] = []
        for row in tickets:
            telegram_id = row.get("telegram_id")
            display = str(row.get("display_name") or "Student")
            if telegram_id:
                display = f"{display} · ID {telegram_id}"
            enriched_tickets.append({**row, "display_name": display})

        return {
            "summary": {
                "users": len(user_rows),
                "active_users_7d": len(
                    {str(row.get("user_id")) for row in active_rows if row.get("user_id")}
                ),
                "requests_today": sum(
                    int(row.get("request_count") or 0) for row in usage_rows
                ),
                "conversations_30d": len(mode_rows),
                "open_tickets": sum(
                    1 for row in enriched_tickets if row.get("status") != "resolved"
                ),
            },
            "users": users,
            "recent_activity": activity,
            "mode_usage": mode_usage,
            "tickets": enriched_tickets,
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
