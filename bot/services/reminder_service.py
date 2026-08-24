"""Timezone-aware, database-backed Telegram study reminders."""

from __future__ import annotations

import logging

from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot.keyboards import reminder_action_keyboard
from bot.services.supabase_service import SupabaseError, SupabaseService

logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, database: SupabaseService) -> None:
        self._database = database

    async def dispatch(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            reminders = await self._database.due_reminders()
        except SupabaseError:
            logger.exception("Could not load due reminders")
            return

        for reminder in reminders:
            if reminder.daily_completed >= reminder.daily_target:
                await self._database.mark_reminder_sent(
                    reminder.user_id, reminder.local_date
                )
                continue
            if reminder.current_streak > 0:
                text = (
                    f"🔥 Keep your {reminder.current_streak}-day streak alive.\n\n"
                    f"Today: {reminder.daily_completed} / {reminder.daily_target}. "
                    "One quick question keeps you moving."
                )
            else:
                text = (
                    "📚 Your quick BuildWise practice is ready.\n\n"
                    f"Today: {reminder.daily_completed} / {reminder.daily_target}."
                )
            try:
                await context.bot.send_message(
                    chat_id=reminder.telegram_id,
                    text=text,
                    reply_markup=reminder_action_keyboard(),
                )
            except Forbidden:
                logger.info(
                    "Disabling reminders for Telegram user who blocked the bot: %s",
                    reminder.telegram_id,
                )
                await self._database.disable_reminder_by_user_id(reminder.user_id)
                continue
            except TelegramError:
                logger.exception("Could not send a BuildWise reminder")
                continue
            await self._database.mark_reminder_sent(
                reminder.user_id, reminder.local_date
            )
