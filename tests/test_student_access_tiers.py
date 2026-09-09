import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from bot.config import Settings
from bot.domain import Mode, UserProfile
from bot.handlers.navigation_bot_handlers import NavigationBotHandlers
from bot.keyboards import follow_up_keyboard
from bot.services.reward_usage_service import RewardAwareSupabaseService


def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


def profile(telegram_id: int = 123, timezone: str = "Africa/Lagos") -> UserProfile:
    return UserProfile.from_row(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "telegram_id": telegram_id,
            "username": "student",
            "display_name": "Student",
            "timezone": timezone,
            "timezone_confirmed": True,
            "timezone_prompted": True,
            "answer_style": "concise",
            "follow_up_buttons": True,
            "save_history": True,
            "default_daily_goal": 5,
        }
    )


def usage_row(limit: int) -> list[dict[str, object]]:
    return [
        {
            "allowed": True,
            "used": 1,
            "limit_value": limit,
            "reservation_id": "00000000-0000-0000-0000-000000000099",
            "source": "daily",
            "bonus_credits_remaining": 0,
        }
    ]


class StudentAccessTierTests(unittest.IsolatedAsyncioTestCase):
    async def test_founder_uses_high_tracked_limit(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._rpc = AsyncMock(return_value=usage_row(1000))  # type: ignore[method-assign]
        with patch.dict(
            os.environ,
            {
                "FOUNDER_TELEGRAM_IDS": "6901754599",
                "FOUNDER_DAILY_AI_REQUEST_LIMIT": "1000",
            },
            clear=False,
        ):
            usage = await service.consume_usage(profile(6901754599), 20)
        self.assertTrue(usage.allowed)
        self.assertEqual(usage.limit, 1000)
        call = service._rpc.await_args
        self.assertEqual(call.args[1]["p_limit"], 1000)

    async def test_pilot_uses_higher_limit(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._rpc = AsyncMock(return_value=usage_row(40))  # type: ignore[method-assign]
        with patch.dict(
            os.environ,
            {
                "PILOT_TELEGRAM_IDS": "555",
                "PILOT_DAILY_AI_REQUEST_LIMIT": "40",
                "FOUNDER_TELEGRAM_IDS": "",
            },
            clear=False,
        ):
            usage = await service.consume_usage(profile(555), 20)
        self.assertEqual(usage.limit, 40)
        call = service._rpc.await_args
        self.assertEqual(call.args[1]["p_limit"], 40)

    async def test_admin_promoted_pilot_uses_higher_limit_until_expiry(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        service._request = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "content": '{"tier":"pilot","expires_at":"' + expires_at + '"}',
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )
        service._rpc = AsyncMock(return_value=usage_row(40))  # type: ignore[method-assign]
        with patch.dict(
            os.environ,
            {
                "PILOT_TELEGRAM_IDS": "",
                "FOUNDER_TELEGRAM_IDS": "",
                "PILOT_DAILY_AI_REQUEST_LIMIT": "40",
            },
            clear=False,
        ):
            usage = await service.consume_usage(profile(777), 20)
        self.assertEqual(usage.limit, 40)
        self.assertEqual(service._rpc.await_args.args[1]["p_limit"], 40)

    async def test_normal_student_keeps_public_limit(self) -> None:
        service = RewardAwareSupabaseService(test_settings())
        service._request = AsyncMock(return_value=[])  # type: ignore[method-assign]
        service._rpc = AsyncMock(return_value=usage_row(20))  # type: ignore[method-assign]
        with patch.dict(
            os.environ,
            {"FOUNDER_TELEGRAM_IDS": "", "PILOT_TELEGRAM_IDS": ""},
            clear=False,
        ):
            usage = await service.consume_usage(profile(777), 20)
        self.assertEqual(usage.limit, 20)
        call = service._rpc.await_args
        self.assertEqual(call.args[1]["p_limit"], 20)


class StudentExperienceControlTests(unittest.TestCase):
    def test_study_followup_has_copy_action(self) -> None:
        keyboard = follow_up_keyboard(Mode.STUDY)
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("followup:copy", callbacks)

    def test_limit_message_has_exact_reset_time_and_timezone(self) -> None:
        text = NavigationBotHandlers._reset_message(profile(), 20)
        self.assertIn("Your limit resets at", text)
        self.assertIn("Africa/Lagos", text)
        self.assertIn("in about", text)


if __name__ == "__main__":
    unittest.main()
