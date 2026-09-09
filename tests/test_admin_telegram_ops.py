import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.admin_telegram import AdminTelegramHandlers, build_admin_telegram_application
from bot.config import Settings
from bot.services.admin_service import AdminService


def settings(**overrides: object) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
    }
    values.update(overrides)
    return Settings(**values)


class AdminTelegramBuildTests(unittest.TestCase):
    def test_admin_bot_is_optional(self) -> None:
        self.assertIsNone(build_admin_telegram_application(settings()))

    def test_admin_bot_uses_separate_configured_token(self) -> None:
        components = build_admin_telegram_application(
            settings(
                ADMIN_TELEGRAM_BOT_TOKEN="987654321:" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJK"
            )
        )
        self.assertIsNotNone(components)
        assert components is not None
        self.assertIsInstance(components.handlers, AdminTelegramHandlers)

    def test_effective_tier_marks_founder_and_dynamic_pilot(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=3)
        with patch.dict(os.environ, {"FOUNDER_TELEGRAM_IDS": "6901754599", "PILOT_TELEGRAM_IDS": ""}):
            founder = AdminService._effective_tier(
                telegram_id=6901754599, user_id="founder", database_pilots={}
            )
            pilot = AdminService._effective_tier(
                telegram_id=555, user_id="pilot", database_pilots={"pilot": future}
            )
        self.assertEqual(founder[0], "Founder")
        self.assertEqual(pilot, ("Pilot", future))


class AdminTelegramCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_users_command_displays_telegram_id(self) -> None:
        with patch.dict(os.environ, {"BUILDWISE_ADMIN_TELEGRAM_IDS": "6901754599"}):
            handlers = AdminTelegramHandlers(settings())
        handlers._service.list_students = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "telegram_id": 555,
                    "username": "student",
                    "name": "Ada",
                    "university": "Yaba College of Technology",
                    "tier": "Pilot",
                    "pilot_until": None,
                    "created_at": "2026-09-09T10:00:00+00:00",
                }
            ]
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=6901754599),
            effective_message=message,
        )
        await handlers.users_command(update, SimpleNamespace())  # type: ignore[arg-type]
        sent = message.reply_text.await_args.args[0]
        self.assertIn("ID: 555", sent)
        self.assertIn("Yaba College of Technology", sent)
        self.assertIn("Pilot", sent)

    async def test_unauthorized_admin_command_is_blocked(self) -> None:
        with patch.dict(os.environ, {"BUILDWISE_ADMIN_TELEGRAM_IDS": "6901754599"}):
            handlers = AdminTelegramHandlers(settings())
        handlers._service.list_students = AsyncMock()  # type: ignore[method-assign]
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=12345),
            effective_message=message,
        )
        await handlers.users_command(update, SimpleNamespace())  # type: ignore[arg-type]
        handlers._service.list_students.assert_not_awaited()
        message.reply_text.assert_awaited_once_with("Admin access required.")


class AdminPilotPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_pilot_persists_temporary_usage_tier(self) -> None:
        service = AdminService(settings())
        service.student_by_telegram_id = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "00000000-0000-0000-0000-000000000001"}
        )
        service._request = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        expires_at = await service.set_pilot(555, days=14)
        self.assertGreater(expires_at, datetime.now(timezone.utc) + timedelta(days=13))
        self.assertEqual(service._request.await_count, 2)
        post_call = service._request.await_args_list[1]
        self.assertEqual(post_call.args[:2], ("POST", "/rest/v1/saved_items"))
        self.assertIn('"tier":"pilot"', post_call.kwargs["json"]["content"])


if __name__ == "__main__":
    unittest.main()
