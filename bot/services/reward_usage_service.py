"""Referral-credit usage orchestration without changing AI/image/PDF handlers."""

from __future__ import annotations

from contextvars import ContextVar
import logging
from typing import Any

from bot.config import Settings
from bot.domain import (
    AnswerStyle,
    ConversationTurn,
    Mode,
    QuizQuestion,
    RequestPayload,
    UsageStatus,
    UserProfile,
)
from bot.services.openai_service import OpenAIService
from bot.services.supabase_service import SupabaseError, SupabaseService, local_date

logger = logging.getLogger(__name__)
_current_reservation: ContextVar[str | None] = ContextVar(
    "buildwise_usage_reservation", default=None
)


class RewardAwareSupabaseService(SupabaseService):
    """Use tiered student allowances, then referral credits, with exact refunds."""

    async def complete_onboarding(
        self, profile: UserProfile, *, display_name: str, university: str
    ) -> UserProfile:
        cleaned_name = " ".join(display_name.split()).strip()
        cleaned_university = " ".join(university.split()).strip()
        if not 2 <= len(cleaned_name) <= 80:
            raise ValueError("Name must be between 2 and 80 characters.")
        if not 2 <= len(cleaned_university) <= 160:
            raise ValueError("University name must be between 2 and 160 characters.")
        rows = await self._request(
            "PATCH",
            "/rest/v1/users",
            params={"id": f"eq.{profile.id}"},
            json={
                "display_name": cleaned_name,
                "university": cleaned_university,
                "onboarding_completed": True,
            },
            headers={"Prefer": "return=representation"},
        )
        if not rows:
            raise SupabaseError("Student onboarding could not be saved")
        return UserProfile.from_row(rows[0])

    async def consume_usage(self, profile: UserProfile, limit: int) -> UsageStatus:
        # Founder/test accounts are deliberately uncapped for product stress-testing.
        # This applies only to the public student experience; BuildWise Pro keeps
        # its own independent limiter and allowlist.
        if profile.telegram_id in self._settings.buildwise_founder_telegram_ids:
            _current_reservation.set(None)
            return UsageStatus(
                allowed=True,
                used=0,
                limit=0,
                reservation_id=None,
                source="founder_unlimited",
                bonus_credits_remaining=0,
            )

        effective_limit = limit
        if profile.telegram_id in self._settings.buildwise_pilot_telegram_ids:
            effective_limit = max(
                limit, self._settings.buildwise_pilot_daily_request_limit
            )

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
