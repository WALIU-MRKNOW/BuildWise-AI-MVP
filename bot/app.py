"""Compose BuildWise services and Telegram handlers without starting the network."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application

from bot.config import Settings
from bot.handlers.bot_handlers import BotHandlers
from bot.handlers.referral_handlers import ReferralHandlers
from bot.handlers.support_handlers import SupportHandlers
from bot.pro.telegram_handlers import TelegramProHandlers
from bot.router import RequestRouter
from bot.services.admin_service import AdminService
from bot.services.referral_service import ReferralService
from bot.services.reminder_service import ReminderService
from bot.services.reward_usage_service import (
    RewardAwareOpenAIService,
    RewardAwareSupabaseService,
)
from bot.services.supabase_service import SupabaseService


@dataclass(frozen=True, slots=True)
class BuildWiseComponents:
    telegram: Application
    handlers: BotHandlers
    database: SupabaseService
    reminders: ReminderService


def build_components(settings: Settings) -> BuildWiseComponents:
    database = RewardAwareSupabaseService(settings)
    reminders = ReminderService(database)
    handlers = BotHandlers(
        settings,
        RewardAwareOpenAIService(settings, database),  # type: ignore[arg-type]
        RequestRouter(),
        database,
        reminders,
    )
    support_handlers = SupportHandlers(database, AdminService(settings))
    referral_handlers = ReferralHandlers(ReferralService(settings), handlers)
    pro_handlers = TelegramProHandlers(settings)
    telegram = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )
    # Referral/support commands are registered first. Pro is then registered before the
    # student catch-all message handler, but only consumes updates while a /pro conversation
    # is active. Existing AI/image/PDF/referral/support handlers remain unchanged.
    referral_handlers.register(telegram)
    support_handlers.register(telegram)
    pro_handlers.register(telegram)
    handlers.register(telegram)
    return BuildWiseComponents(telegram, handlers, database, reminders)
