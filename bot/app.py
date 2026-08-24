"""Compose BuildWise services and Telegram handlers without starting the network."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application

from bot.config import Settings
from bot.handlers.bot_handlers import BotHandlers
from bot.handlers.referral_handlers import ReferralHandlers
from bot.handlers.support_handlers import SupportHandlers
from bot.router import RequestRouter
from bot.services.admin_service import AdminService
from bot.services.openai_service import OpenAIService
from bot.services.referral_service import ReferralService
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
    support_handlers = SupportHandlers(database, AdminService(settings))
    referral_handlers = ReferralHandlers(ReferralService(settings), handlers)
    telegram = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )
    # Referral/support commands are registered first. Referral /start delegates
    # to the existing start handler after attribution; AI/image/PDF handlers remain unchanged.
    referral_handlers.register(telegram)
    support_handlers.register(telegram)
    handlers.register(telegram)
    return BuildWiseComponents(telegram, handlers, database, reminders)
