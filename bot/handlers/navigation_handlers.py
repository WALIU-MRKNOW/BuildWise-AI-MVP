"""Thin navigation callbacks that route buttons into existing BuildWise handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.navigation_bot_handlers import NavigationBotHandlers
from bot.handlers.support_handlers import AWAITING_SUPPORT_MESSAGE, SupportHandlers
from bot.pro.telegram_beta_handlers import TelegramProBetaHandlers


class NavigationSupportHandlers(SupportHandlers):
    """Expose the existing support conversation through a home-menu button."""

    def register(self, application: Application) -> None:
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CommandHandler("support", self.support_command),
                    CallbackQueryHandler(self.support_button, pattern=r"^nav:support$"),
                ],
                states={
                    AWAITING_SUPPORT_MESSAGE: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.submit_support)
                    ]
                },
                fallbacks=[CommandHandler("cancel", self.cancel_support)],
                name="buildwise-support",
                persistent=False,
            ),
            group=0,
        )

    async def support_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query is not None:
            await query.answer()
        return await self.support_command(update, context)


class NavigationHandlers:
    """Route safe utility buttons without duplicating the underlying commands."""

    def __init__(
        self,
        student_handlers: NavigationBotHandlers,
        pro_handlers: TelegramProBetaHandlers,
    ) -> None:
        self._student = student_handlers
        self._pro = pro_handlers

    def register(self, application: Application) -> None:
        application.add_handler(
            CallbackQueryHandler(
                self.utility_button,
                pattern=r"^nav:(myid|pro_status|pro_beta_stats)$",
            )
        )

    async def utility_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        action = (query.data or "").split(":", 1)[1]
        if action == "myid":
            await self._pro.myid(update, context)
        elif action == "pro_status":
            await self._pro.pro_status(update, context)
        elif action == "pro_beta_stats":
            await self._pro.pro_beta_stats(update, context)
