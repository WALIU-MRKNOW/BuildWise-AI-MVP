"""Compose BuildWise services and Telegram handlers without starting the network."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application

from bot.config import Settings
from bot.handlers.bot_handlers import BotHandlers
from bot.router import RequestRouter
from bot.services.openai_service import OpenAIService
from bot.services.reminder_service import ReminderService
from bot.services.supabase_service import SupabaseService


@dataclass(frozen=True, slots=True)
class BuildWiseComponents:
    telegram: Application
    handlers: BotHandlers
    database: SupabaseService
    reminders: ReminderService


def build_components(settings: Settings) -> BuildWiseComponents:
    database = SupabaseService(settings)
    reminders = ReminderService(database)
    handlers = BotHandlers(
        settings,
        OpenAIService(settings),
        RequestRouter(),
        database,
        reminders,
    )
    telegram = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )
    handlers.register(telegram)
    return BuildWiseComponents(telegram, handlers, database, reminders)
