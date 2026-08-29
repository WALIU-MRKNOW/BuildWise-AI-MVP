from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from telegram.ext import ConversationHandler

from bot.config import Settings
from bot.pro.telegram_handlers import PROJECT, PRO_STATE_KEY
from bot.pro.telegram_menu_handlers import INTRO, TelegramProMenuHandlers


AUTHORIZED_ID = 6901754599


def settings(*, enabled: bool = True, allowed: str = str(AUTHORIZED_ID)) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=enabled,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=allowed,
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=10,
    )


def message():
    return SimpleNamespace(reply_text=AsyncMock())


def callback():
    return SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())


def update(*, msg=None, query=None, user_id: int = AUTHORIZED_ID):
    return SimpleNamespace(
        effective_message=msg,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )


def context(*, user_data=None):
    return SimpleNamespace(user_data=user_data or {})


class BuildWiseProMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_pro_starts_with_button_menu(self) -> None:
        handlers = TelegramProMenuHandlers(settings())
        msg = message()
        ctx = context(user_data={"mode": "study"})

        result = await handlers.start(update(msg=msg), ctx)

        self.assertEqual(result, INTRO)
        self.assertIn(PRO_STATE_KEY, ctx.user_data)
        self.assertEqual(ctx.user_data["mode"], "study")
        kwargs = msg.reply_text.await_args.kwargs
        markup = kwargs["reply_markup"]
        labels = [row[0].text for row in markup.inline_keyboard]
        callbacks = [row[0].callback_data for row in markup.inline_keyboard]
        self.assertEqual(labels, ["▶️ Start Pro Report", "ℹ️ How It Works", "✖️ Cancel"])
        self.assertEqual(
            callbacks,
            ["pro:intro:start", "pro:intro:how", "pro:cancel"],
        )

    async def test_start_button_enters_project_step(self) -> None:
        handlers = TelegramProMenuHandlers(settings())
        q = callback()
        result = await handlers.begin_report(update(query=q), context())
        self.assertEqual(result, PROJECT)
        self.assertIn("Step 1 of 6", q.edit_message_text.await_args.args[0])

    async def test_how_it_works_keeps_intro_and_shows_start_button(self) -> None:
        handlers = TelegramProMenuHandlers(settings())
        q = callback()
        result = await handlers.how_it_works(update(query=q), context())
        self.assertEqual(result, INTRO)
        self.assertIn("PDF + DOCX", q.edit_message_text.await_args.args[0])
        markup = q.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "pro:intro:start")

    async def test_disabled_and_unauthorized_still_blocked(self) -> None:
        disabled = TelegramProMenuHandlers(settings(enabled=False))
        msg = message()
        self.assertEqual(
            await disabled.start(update(msg=msg), context()), ConversationHandler.END
        )

        restricted = TelegramProMenuHandlers(settings(allowed="9543030600"))
        msg2 = message()
        self.assertEqual(
            await restricted.start(update(msg=msg2), context()), ConversationHandler.END
        )
        self.assertIn("restricted professional beta", msg2.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
