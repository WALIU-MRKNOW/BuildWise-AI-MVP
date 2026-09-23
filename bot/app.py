"""Compose BuildWise services and Telegram handlers without starting the network."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application

from bot.config import Settings
from bot.handlers.sketch_report_handlers import SketchReportHandlers
from bot.handlers.student_sketch_handlers import StudentSketchHandlers
from bot.handlers.repository_audit_handlers import RepositoryAuditHandlers
from bot.handlers.bot_handlers import BotHandlers
from bot.handlers.navigation_handlers import NavigationHandlers, NavigationSupportHandlers
from bot.handlers.student_learning_handlers import StudentLearningHandlers
from bot.pro.telegram_beta_handlers import TelegramProBetaHandlers
from bot.router import RequestRouter
from bot.services.admin_service import AdminService
from bot.services.openai_service import OpenAIService
from bot.services.reminder_service import ReminderService
from bot.services.reward_usage_service import StudentAccessSupabaseService
from bot.services.supabase_service import SupabaseService


@dataclass(frozen=True, slots=True)
class BuildWiseComponents:
    telegram: Application
    handlers: BotHandlers
    database: SupabaseService
    reminders: ReminderService


def build_components(settings: Settings) -> BuildWiseComponents:
    database = StudentAccessSupabaseService(settings)
    reminders = ReminderService(database)
    handlers = RepositoryAuditHandlers(
        settings,
        OpenAIService(settings),
        RequestRouter(),
        database,
        reminders,
    )
    support_handlers = NavigationSupportHandlers(database, AdminService(settings))
    student_learning_handlers = StudentLearningHandlers()
    pro_handlers = TelegramProBetaHandlers(settings)
    navigation_handlers = NavigationHandlers(handlers, pro_handlers)
    telegram = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )
    # Support/Pro/navigation/student-learning callbacks are registered before the
    # student catch-all so existing workflows remain isolated.
    support_handlers.register(telegram)
    pro_handlers.register(telegram)
    navigation_handlers.register(telegram)
    student_learning_handlers.register(telegram)
    SketchReportHandlers().register(telegram)
    StudentSketchHandlers(settings, database).register(telegram)
    handlers.register(telegram)
    return BuildWiseComponents(telegram, handlers, database, reminders)
