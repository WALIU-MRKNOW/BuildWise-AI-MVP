"""Telegram /support flow isolated from BuildWise AI/image/PDF handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.services.admin_service import AdminService, AdminServiceError
from bot.services.supabase_service import SupabaseService

AWAITING_SUPPORT_MESSAGE = 1


class SupportHandlers:
    def __init__(self, database: SupabaseService, admin_service: AdminService) -> None:
        self._database = database
        self._admin = admin_service

    def register(self, application: Application) -> None:
        application.add_handler(
            ConversationHandler(
                entry_points=[CommandHandler("support", self.support_command)],
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

    async def support_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        message = update.effective_message
        if message is None:
            return ConversationHandler.END
        if context.args:
            await self._create_ticket(update, " ".join(context.args))
            return ConversationHandler.END
        await message.reply_text(
            "🛟 BUILDWISE SUPPORT\n\n"
            "Send one message describing the problem, question, or feedback. "
            "Please do not include passwords, API keys, or other secrets.\n\n"
            "Use /cancel to stop."
        )
        return AWAITING_SUPPORT_MESSAGE

    async def submit_support(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        message = update.effective_message
        text = (message.text if message else "") or ""
        await self._create_ticket(update, text)
        return ConversationHandler.END

    async def cancel_support(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if update.effective_message:
            await update.effective_message.reply_text("Support request cancelled.")
        return ConversationHandler.END

    async def _create_ticket(self, update: Update, text: str) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        cleaned = text.strip()
        if not cleaned:
            await message.reply_text("Please send a short description of what you need help with.")
            return
        if len(cleaned) > 4000:
            await message.reply_text("Please keep the support message under 4,000 characters.")
            return
        profile = await self._database.upsert_user(
            user.id, user.username, user.full_name or "Student"
        )
        try:
            ticket_id = await self._admin.create_support_ticket(
                user_id=profile.id,
                telegram_id=user.id,
                username=user.username,
                display_name=user.full_name or "Student",
                message=cleaned,
            )
        except (AdminServiceError, ValueError):
            await message.reply_text(
                "I couldn't create the support ticket right now. Please try /support again."
            )
            return
        await message.reply_text(
            "✅ Support ticket created.\n\n"
            f"Ticket: {ticket_id[:8]}\n"
            "Status: Open\n\n"
            "The BuildWise team can now review it from the private admin dashboard."
        )
