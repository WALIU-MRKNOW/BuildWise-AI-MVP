"""Navigation handler that routes onboarding alerts through the private admin bot."""

from __future__ import annotations

import logging

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.handlers.navigation_bot_handlers import NavigationBotHandlers

logger = logging.getLogger(__name__)


class AdminAwareNavigationBotHandlers(NavigationBotHandlers):
    """Keep student behavior unchanged while using a dedicated admin bot for alerts."""

    async def _notify_admin_new_student(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        update: Update,
        identity: dict[str, str],
    ) -> None:
        token_setting = self._settings.admin_telegram_bot_token
        if token_setting is None or not token_setting.get_secret_value().strip():
            await super()._notify_admin_new_student(context, update, identity)
            return
        if not self._admin_notify_ids:
            return
        user = update.effective_user
        if user is None:
            return
        username = f"@{user.username}" if user.username else "No username"
        text = (
            "🆕 New BuildWise student\n\n"
            f"Name: {identity['name']}\n"
            f"Institution: {identity['university']}\n"
            f"Telegram: {username}\n"
            f"Telegram ID: {user.id}"
        )
        token = token_setting.get_secret_value().strip()
        try:
            async with Bot(token=token) as admin_bot:
                for admin_id in self._admin_notify_ids:
                    try:
                        await admin_bot.send_message(chat_id=admin_id, text=text)
                    except TelegramError:
                        logger.warning(
                            "Could not send admin-bot onboarding alert admin_id=%s",
                            admin_id,
                        )
        except TelegramError:
            logger.exception("BuildWise admin bot could not send onboarding alerts")
