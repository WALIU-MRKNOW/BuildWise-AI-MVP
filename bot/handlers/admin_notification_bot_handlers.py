"""Route new-student join alerts through the optional BuildWise admin Telegram bot."""

from __future__ import annotations

import logging
import os

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.handlers.navigation_bot_handlers import NavigationBotHandlers

logger = logging.getLogger(__name__)


class AdminNotificationBotHandlers(NavigationBotHandlers):
    """Use a separate Telegram bot for admin join alerts when configured.

    Student conversations continue to use the normal BuildWise bot. The optional
    ADMIN_TELEGRAM_BOT_TOKEN is outbound-only and is used only for private admin
    notifications. If it is unavailable or fails, BuildWise falls back to the
    existing main-bot notification path.
    """

    async def _notify_admin_new_student(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        update: Update,
        identity: dict[str, str],
    ) -> None:
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

        admin_token = os.getenv("ADMIN_TELEGRAM_BOT_TOKEN", "").strip()
        if admin_token:
            try:
                async with Bot(token=admin_token) as admin_bot:
                    for admin_id in self._admin_notify_ids:
                        await admin_bot.send_message(chat_id=admin_id, text=text)
                return
            except TelegramError:
                logger.exception(
                    "Separate admin Telegram notification failed; falling back to main bot"
                )

        await super()._notify_admin_new_student(context, update, identity)
