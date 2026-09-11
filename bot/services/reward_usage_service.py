"""Student access-tier usage orchestration and onboarding helpers.

The legacy class names are kept temporarily to avoid breaking existing imports while
referral rewards are retired. Usage is now limited only by the configured normal,
pilot, or founder allowance.
"""

from __future__ import annotations

import json
import os
from typing import Any

from bot.config import Settings
from bot.domain import AnswerStyle, ConversationTurn, Mode, QuizQuestion, RequestPayload, UsageStatus, UserProfile
from bot.services.openai_service import OpenAIService
from bot.services.supabase_service import SupabaseError, SupabaseService, local_date

_STUDENT_PROFILE_ITEM_TYPE = "student_profile"
_STUDENT_PROFILE_TITLE = "BuildWise student profile"


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


class RewardAwareSupabaseService(SupabaseService):
    """Backward-compatible tier-aware service with no referral-credit path."""

    async def consume_usage(self, profile: UserProfile, limit: int) -> UsageStatus:
        if profile.telegram_id in founder_telegram_ids():
            effective_limit = founder_daily_limit(limit)
        elif profile.telegram_id in pilot_telegram_ids():
            effective_limit = pilot_daily_limit(limit)
        else:
            effective_limit = limit

        rows = await self._rpc(
            "consume_daily_usage",
            {
                "p_user_id": profile.id,
                "p_usage_date": local_date(profile.timezone).isoformat(),
                "p_limit": effective_limit,
            },
        )
        row = rows[0] if isinstance(rows, list) else rows
        return UsageStatus(
            allowed=bool(row["allowed"]),
            used=int(row["used"]),
            limit=int(row["limit_value"]),
        )

    async def refund_usage(self, profile: UserProfile) -> None:
        await self._rpc(
            "refund_daily_usage",
            {
                "p_user_id": profile.id,
                "p_usage_date": local_date(profile.timezone).isoformat(),
            },
        )

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
        payload = json.dumps(
            {"name": name.strip(), "university": university.strip()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self._request(
            "DELETE",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"eq.{_STUDENT_PROFILE_ITEM_TYPE}",
            },
        )
        await self._request(
            "POST",
            "/rest/v1/saved_items",
            json={
                "user_id": user_id,
                "item_type": _STUDENT_PROFILE_ITEM_TYPE,
                "title": _STUDENT_PROFILE_TITLE,
                "content": payload,
                "file_reference": None,
            },
            headers={"Prefer": "return=minimal"},
        )

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

    async def delete_history(self, user_id: str) -> int:
        """Delete student work while preserving required onboarding identity."""
        conversations = await self.recent_conversations(user_id, limit=500)
        references = {
            row.get("file_reference") for row in conversations if row.get("file_reference")
        }
        for reference in references:
            try:
                await self.delete_file(str(reference))
            except SupabaseError:
                pass
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


class RewardAwareOpenAIService:
    """Backward-compatible OpenAI wrapper after retirement of referral rewards."""

    def __init__(self, settings: Settings, database: RewardAwareSupabaseService) -> None:
        self._delegate = OpenAIService(settings)
        self._database = database

    async def answer(
        self,
        mode: Mode,
        payload: RequestPayload,
        *,
        history: list[ConversationTurn] | None = None,
        answer_style: AnswerStyle = AnswerStyle.CONCISE,
    ) -> str:
        return await self._delegate.answer(
            mode, payload, history=history, answer_style=answer_style
        )

    async def generate_quiz(
        self, *, topic: str, difficulty: str, count: int
    ) -> list[QuizQuestion]:
        return await self._delegate.generate_quiz(
            topic=topic, difficulty=difficulty, count=count
        )
