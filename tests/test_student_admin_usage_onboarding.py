from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bot.admin import _dashboard_html
from bot.config import Settings
from bot.domain import Mode, UserProfile
from bot.handlers.navigation_bot_handlers import NavigationBotHandlers
from bot.keyboards import follow_up_keyboard
from bot.services.admin_service import AdminService
from bot.services.reward_usage_service import RewardAwareSupabaseService


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
    }
    values.update(overrides)
    return Settings(**values)


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


class StudentUsageTierTests(unittest.IsolatedAsyncioTestCase):
    async def test_founder_student_usage_is_uncapped(self) -> None:
        service = RewardAwareSupabaseService(settings())
        service._rpc = AsyncMock()  # type: ignore[method-assign]

        usage = await service.consume_usage(profile(6901754599), 20)

        self.assertTrue(usage.allowed)
        self.assertEqual(usage.source, "founder_unlimited")
        service._rpc.assert_not_awaited()

    async def test_pilot_gets_higher_temporary_allowance(self) -> None:
        service = RewardAwareSupabaseService(
            settings(
                BUILDWISE_PILOT_TELEGRAM_IDS="123",
                BUILDWISE_PILOT_DAILY_REQUEST_LIMIT=40,
            )
        )
        service._rpc = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "allowed": True,
                    "used": 1,
                    "limit_value": 40,
                    "reservation_id": "00000000-0000-0000-0000-000000000099",
                    "source": "daily",
                    "bonus_credits_remaining": 0,
                }
            ]
        )

        usage = await service.consume_usage(profile(123), 20)

        self.assertEqual(usage.limit, 40)
        call = service._rpc.await_args
        self.assertEqual(call.args[1]["p_limit"], 40)

    async def test_normal_student_keeps_public_limit(self) -> None:
        service = RewardAwareSupabaseService(settings())
        service._rpc = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "allowed": True,
                    "used": 1,
                    "limit_value": 20,
                    "reservation_id": "00000000-0000-0000-0000-000000000099",
                    "source": "daily",
                    "bonus_credits_remaining": 0,
                }
            ]
        )

        await service.consume_usage(profile(123), 20)
        call = service._rpc.await_args
        self.assertEqual(call.args[1]["p_limit"], 20)

    def test_reset_label_is_exact_and_timezone_aware(self) -> None:
        label = NavigationBotHandlers._reset_label(profile())
        self.assertIn("12:00 AM", label)
        self.assertIn("Africa/Lagos", label)


class StudentOnboardingTests(unittest.TestCase):
    def test_old_rows_remain_compatible_before_migration(self) -> None:
        existing = profile()
        self.assertTrue(existing.onboarding_completed)
        self.assertIsNone(existing.university)

    def test_migration_adds_required_profile_fields_and_preserves_existing_users(self) -> None:
        sql = Path("migrations/006_student_onboarding.sql").read_text().lower()
        self.assertIn("university text", sql)
        self.assertIn("onboarding_completed boolean", sql)
        self.assertIn("update public.users", sql)
        self.assertIn("set onboarding_completed = true", sql)
        self.assertIn("when users.onboarding_completed", sql)


class CopyAndAdminExperienceTests(unittest.TestCase):
    def test_study_keyboard_has_copy_action(self) -> None:
        callbacks = [
            button.callback_data
            for row in follow_up_keyboard(Mode.STUDY).inline_keyboard
            for button in row
        ]
        self.assertIn("followup:copy", callbacks)

    def test_admin_dashboard_exposes_ids_institution_search_and_activity(self) -> None:
        html = _dashboard_html("csrf-test")
        self.assertIn("Telegram ID", html)
        self.assertIn("Institution", html)
        self.assertIn('id="userSearch"', html)
        self.assertIn("Recent activity", html)
        self.assertIn("Last seen", html)

    def test_admin_service_returns_all_users_and_more_activity(self) -> None:
        source = inspect.getsource(AdminService.dashboard)
        self.assertIn('"limit": "10000"', source)
        self.assertIn('"limit": "100"', source)
        self.assertNotIn("[:30]", source)


if __name__ == "__main__":
    unittest.main()
