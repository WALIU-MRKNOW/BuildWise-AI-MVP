"""Compose BuildWise services and Telegram handlers without starting the network."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application

from bot.config import Settings
from bot.handlers.sketch_report_handlers import SketchReportHandlers
from bot.handlers.admin_notification_bot_handlers import AdminNotificationBotHandlers
from bot.handlers.bot_handlers import BotHandlers
from bot.handlers.navigation_handlers import NavigationHandlers, NavigationSupportHandlers
from bot.handlers.referral_handlers import ReferralHandlers
from bot.handlers.student_learning_handlers import StudentLearningHandlers
from bot.pro.telegram_beta_handlers import TelegramProBetaHandlers
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
    handlers = AdminNotificationBotHandlers(
        settings,
        RewardAwareOpenAIService(settings, database),  # type: ignore[arg-type]
        RequestRouter(),
        database,
        reminders,
    )
    support_handlers = NavigationSupportHandlers(database, AdminService(settings))
    referral_handlers = ReferralHandlers(ReferralService(settings), handlers)
    student_learning_handlers = StudentLearningHandlers()
    pro_handlers = TelegramProBetaHandlers(settings)
    navigation_handlers = NavigationHandlers(handlers, pro_handlers)
    telegram = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )
    # Specific referral/support/Pro/navigation/student-learning callbacks are
    # registered before the student catch-all so existing workflows remain isolated.
    referral_handlers.register(telegram)
    support_handlers.register(telegram)
    pro_handlers.register(telegram)
    navigation_handlers.register(telegram)
    student_learning_handlers.register(telegram)
    SketchReportHandlers().register(telegram)
    handlers.register(telegram)
    return BuildWiseComponents(telegram, handlers, database, reminders)
