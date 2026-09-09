import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_notification_bot_handlers import AdminNotificationBotHandlers


class _FakeAdminBot:
    sent: list[tuple[int, str]] = []

    def __init__(self, token: str) -> None:
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send_message(self, *, chat_id: int, text: str):
        self.sent.append((chat_id, text))


class AdminTelegramNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_admin_bot_sends_join_alert(self) -> None:
        handler = object.__new__(AdminNotificationBotHandlers)
        handler._admin_notify_ids = frozenset({9001})
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456, username="student_user")
        )
        identity = {"name": "Student Name", "university": "Example University"}
        _FakeAdminBot.sent = []

        with patch.dict(os.environ, {"ADMIN_TELEGRAM_BOT_TOKEN": "admin-token"}, clear=False), patch(
            "bot.handlers.admin_notification_bot_handlers.Bot", _FakeAdminBot
        ):
            await handler._notify_admin_new_student(context, update, identity)

        self.assertEqual(len(_FakeAdminBot.sent), 1)
        chat_id, text = _FakeAdminBot.sent[0]
        self.assertEqual(chat_id, 9001)
        self.assertIn("Student Name", text)
        self.assertIn("Example University", text)
        self.assertIn("Telegram ID: 123456", text)
        context.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
