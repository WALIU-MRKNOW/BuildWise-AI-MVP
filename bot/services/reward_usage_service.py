"""Referral-credit usage orchestration without changing AI/image/PDF handlers."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

from bot.config import Settings
from bot.domain import AnswerStyle, ConversationTurn, Mode, QuizQuestion, RequestPayload, UsageStatus, UserProfile
from bot.services.openai_service import OpenAIService
from bot.services.supabase_service import SupabaseError, SupabaseService, local_date

logger = logging.getLogger(__name__)
_current_reservation: ContextVar[str | None] = ContextVar(
    "buildwise_usage_reservation", default=None
)

_STUDENT_PROFILE_ITEM_TYPE = "student_profile"
_STUDENT_PROFILE_TITLE = "BuildWise student profile"
_USAGE_TIER_ITEM_TYPE = "usage_tier"
_INTERNAL_ITEM_TYPES = (_STUDENT_PROFILE_ITEM_TYPE, _USAGE_TIER_ITEM_TYPE)


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


class RewardAwareSupabaseService(SupabaseService):
    """Use daily allowance first, then referral credits, with exact refunds."""

    async def _database_pilot_active(self, user_id: str) -> bool:
        rows = await self._request(
            "GET",
            "/rest/v1/saved_items",
            params={
                "user_id": f"eq.{user_id}",
                "item_type": f"eq.{_USAGE_TIER_ITEM_TYPE}",
                "select": "content,created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return False
        try:
            payload = json.loads(str(rows[0].get("content") or "{}"))
        except json.JSONDecodeError:
            return False
        if str(payload.get("tier") or "").lower() != "pilot":
            return False
        expires_at = _parse_timestamp(payload.get("expires_at"))
        if expires_at is None:
            return False
        if expires_at <= datetime.now(timezone.utc):
            try:
                await self._request(
                    "DELETE",
                    "/rest/v1/saved_items",
                    params={
                        "user_id": f"eq.{user_id}",
                        "item_type": f"eq.{_USAGE_TIER_ITEM_TYPE}",
                    },
                )
            except SupabaseError:
                logger.warning("Could not clean expired pilot tier user_id=%s", user_id)
            return False
        return True

    async def consume_usage(self, profile: UserProfile, limit: int) -> UsageStatus:
        if profile.telegram_id in founder_telegram_ids():
            effective_limit = founder_daily_limit(limit)
        elif (
            profile.telegram_id in pilot_telegram_ids()
            or await self._database_pilot_active(profile.id)
        ):
            effective_limit = pilot_daily_limit(limit)
        else:
            effective_limit = limit

        rows = await self._rpc(
            "consume_usage_with_referral_credit",
            {
                "p_user_id": profile.id,
                "p_usage_date": local_date(profile.timezone).isoformat(),
                "p_limit": effective_limit,
            },
        )
        row = rows[0] if isinstance(rows, list) else rows
        reservation_id = str(row["reservation_id"]) if row.get("reservation_id") else None
        _current_reservation.set(reservation_id)
        return UsageStatus(
            allowed=bool(row["allowed"]),
            used=int(row["used"]),
            limit=int(row["limit_value"]),
            reservation_id=reservation_id,
            source=row.get("source"),
            bonus_credits_remaining=int(row.get("bonus_credits_remaining") or 0),
        )

    async def refund_usage(self, profile: UserProfile) -> None:
        reservation_id = _current_reservation.get()
        if reservation_id is None:
            return
        try:
            await self._rpc(
                "refund_usage_reservation", {"p_reservation_id": reservation_id}
            )
        finally:
            _current_reservation.set(None)

    async def finalize_current_usage(self) -> dict[str, Any] | None:
        reservation_id = _current_reservation.get()
        if reservation_id is None:
            return None
        try:
            rows = await self._rpc(
                "finalize_usage_and_reward_referrer",
                {"p_reservation_id": reservation_id, "p_reward_credits": 10},
            )
            row = rows[0] if isinstance(rows, list) and rows else rows
            return row if isinstance(row, dict) else None
        finally:
            _current_reservation.set(None)

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
                "item_type": "not.in.(student_profile,usage_tier)",
                "select": "id,item_type,title,content,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def delete_history(self, user_id: str) -> int:
        """Delete student work while preserving onboarding and admin tier metadata."""
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
                "item_type": "not.in.(student_profile,usage_tier)",
            },
        )
        return len(conversations)


class RewardAwareOpenAIService:
    """Finalize referral qualification only after the AI call succeeds."""

    def __init__(self, settings: Settings, database: RewardAwareSupabaseService) -> None:
        self._delegate = OpenAIService(settings)
        self._database = database

    async def _finalize(self) -> None:
        try:
            result = await self._database.finalize_current_usage()
            if result and result.get("reward_granted"):
                logger.info(
                    "Referral reward granted user_id=%s credits=%s",
                    result.get("rewarded_user_id"),
                    result.get("credits_awarded"),
                )
        except SupabaseError:
            # The AI request already succeeded. Reward bookkeeping must not turn
            # a successful answer into a user-visible AI failure.
            logger.exception("AI request succeeded but referral reward finalization failed")

    async def answer(
        self,
        mode: Mode,
        payload: RequestPayload,
        *,
        history: list[ConversationTurn] | None = None,
        answer_style: AnswerStyle = AnswerStyle.CONCISE,
    ) -> str:
        result = await self._delegate.answer(
            mode, payload, history=history, answer_style=answer_style
        )
        await self._finalize()
        return result

    async def generate_quiz(
        self, *, topic: str, difficulty: str, count: int
    ) -> list[QuizQuestion]:
        result = await self._delegate.generate_quiz(
            topic=topic, difficulty=difficulty, count=count
        )
        await self._finalize()
        return result
