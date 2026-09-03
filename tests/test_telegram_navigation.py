from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from telegram.ext import ConversationHandler

from bot.config import Settings
from bot.handlers.navigation_bot_handlers import navigation_home_keyboard, study_tools_keyboard
from bot.handlers.navigation_handlers import NavigationHandlers, NavigationSupportHandlers
from bot.pro.telegram_menu_handlers import INTRO, TelegramProMenuHandlers


TEST_ID = 6901754599


def settings(*, allowed: str = str(TEST_ID), enabled: bool = True) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=enabled,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=allowed,
    )


def callback_update(data: str, *, user_id: int = TEST_ID):
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(data=data, answer=AsyncMock(), message=message)
    return SimpleNamespace(
        callback_query=query,
        effective_message=message,
        effective_user=SimpleNamespace(
            id=user_id,
            username="waliu344",
            first_name="WALIU",
        ),
    )


class NavigationKeyboardTests(unittest.TestCase):
    def test_public_home_hides_private_pro_and_admin_buttons(self) -> None:
        markup = navigation_home_keyboard(
            timezone_confirmed=True,
            pro_access=False,
            admin_access=False,
        )
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🎓 Ask BuildWise", labels)
        self.assertIn("🆔 My ID", labels)
        self.assertIn("🛟 Support", labels)
        self.assertNotIn("🏗 BuildWise Pro", labels)
        self.assertNotIn("📊 Pro Status", labels)
        self.assertNotIn("📈 Beta Stats", labels)

    def test_pro_and_admin_home_buttons_are_role_aware(self) -> None:
        pro_markup = navigation_home_keyboard(
            timezone_confirmed=True,
            pro_access=True,
            admin_access=False,
        )
        pro_labels = [button.text for row in pro_markup.inline_keyboard for button in row]
        self.assertIn("🏗 BuildWise Pro", pro_labels)
        self.assertIn("📊 Pro Status", pro_labels)
        self.assertNotIn("📈 Beta Stats", pro_labels)

        admin_markup = navigation_home_keyboard(
            timezone_confirmed=True,
            pro_access=True,
            admin_access=True,
        )
        admin_labels = [button.text for row in admin_markup.inline_keyboard for button in row]
        self.assertIn("📈 Beta Stats", admin_labels)

    def test_study_tools_menu_exposes_existing_student_controls(self) -> None:
        markup = study_tools_keyboard()
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("home:history", callbacks)
        self.assertIn("home:progress", callbacks)
        self.assertIn("settings:goal", callbacks)
        self.assertIn("settings:reminders", callbacks)
        self.assertIn("home:settings", callbacks)
        self.assertIn("home:menu", callbacks)


class ProNavigationEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pro_button_enters_existing_pro_conversation(self) -> None:
        handler = TelegramProMenuHandlers(settings())
        update = callback_update("nav:pro")
        context = SimpleNamespace(user_data={})

        result = await handler.start_from_button(update, context)

        self.assertEqual(result, INTRO)
        update.callback_query.answer.assert_awaited_once()
        self.assertIn("BuildWise Pro", update.effective_message.reply_text.await_args.args[0])


class NavigationUtilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_utility_buttons_delegate_to_existing_pro_commands(self) -> None:
        pro = SimpleNamespace(
            myid=AsyncMock(),
            pro_status=AsyncMock(),
            pro_beta_stats=AsyncMock(),
        )
        router = NavigationHandlers(SimpleNamespace(), pro)
        context = SimpleNamespace(user_data={})

        update = callback_update("nav:myid")
        await router.utility_button(update, context)
        pro.myid.assert_awaited_once_with(update, context)

        update2 = callback_update("nav:pro_status")
        await router.utility_button(update2, context)
        pro.pro_status.assert_awaited_once_with(update2, context)

        update3 = callback_update("nav:pro_beta_stats")
        await router.utility_button(update3, context)
        pro.pro_beta_stats.assert_awaited_once_with(update3, context)

    async def test_support_button_starts_existing_support_conversation(self) -> None:
        support = NavigationSupportHandlers(SimpleNamespace(), SimpleNamespace())
        support.support_command = AsyncMock(return_value=1)
        update = callback_update("nav:support")
        context = SimpleNamespace(user_data={})

        result = await support.support_button(update, context)

        self.assertEqual(result, 1)
        update.callback_query.answer.assert_awaited_once()
        support.support_command.assert_awaited_once_with(update, context)


if __name__ == "__main__":
    unittest.main()
