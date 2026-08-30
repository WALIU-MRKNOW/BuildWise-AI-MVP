"""Button-first Telegram entry menu for the private BuildWise Pro beta."""

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

from bot.pro.telegram_document_handlers import TelegramProDocumentHandlers
from bot.pro.telegram_handlers import (
    CONFIRM,
    DRAWING,
    NOTES,
    PROJECT,
    PRO_STATE_KEY,
    STANDARD,
    TEMPLATE,
    _buttons,
)

INTRO = 6


class TelegramProMenuHandlers(TelegramProDocumentHandlers):
    """Add a button-first Pro menu while preserving the existing report workflow."""

    def register(self, application: Application) -> None:
        invalid = MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_step)
        cancel_button = CallbackQueryHandler(self.cancel_callback, pattern=r"^pro:cancel$")
        conversation = ConversationHandler(
            entry_points=[
                CommandHandler("pro", self.start),
                CallbackQueryHandler(self.start_from_button, pattern=r"^nav:pro$"),
            ],
            states={
                INTRO: [
                    CallbackQueryHandler(self.begin_report, pattern=r"^pro:intro:start$"),
                    CallbackQueryHandler(self.how_it_works, pattern=r"^pro:intro:how$"),
                    cancel_button,
                    invalid,
                ],
                PROJECT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.project_information),
                    invalid,
                ],
                DRAWING: [
                    MessageHandler(filters.PHOTO | filters.Document.ALL, self.drawing),
                    invalid,
                ],
                NOTES: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.site_notes),
                    invalid,
                ],
                TEMPLATE: [
                    CallbackQueryHandler(self.template_skip, pattern=r"^pro:template:skip$"),
                    cancel_button,
                    MessageHandler(filters.Document.ALL, self.template),
                    invalid,
                ],
                STANDARD: [
                    CallbackQueryHandler(self.standard_skip, pattern=r"^pro:standard:skip$"),
                    cancel_button,
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.standard_reference),
                    invalid,
                ],
                CONFIRM: [
                    CallbackQueryHandler(self.generate, pattern=r"^pro:generate$"),
                    cancel_button,
                    invalid,
                ],
            },
            fallbacks=[
                CommandHandler("pro_cancel", self.cancel),
                CommandHandler("cancel", self.cancel),
            ],
            allow_reentry=True,
            name="buildwise-pro-report-beta",
            persistent=False,
        )
        application.add_handler(conversation)
        application.add_handler(CommandHandler("pro_cancel", self.cancel))

    async def start_from_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query is not None:
            await query.answer()
        return await self.start(update, context)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        if message is None:
            return ConversationHandler.END
        if not self._settings.buildwise_pro_enabled:
            await message.reply_text(
                "BuildWise Pro is not enabled yet. Your student BuildWise features are unchanged."
            )
            return ConversationHandler.END
        if not self._authorized(update):
            await message.reply_text(
                "BuildWise Pro is currently a restricted professional beta and is not enabled for this account."
            )
            return ConversationHandler.END

        context.user_data.pop(PRO_STATE_KEY, None)
        self._state(context)
        await message.reply_text(
            "🧰 BUILDWISE PRO\n"
            "Professional Report Generator\n\n"
            "Create a structured draft professional report from project information, drawings/sketches, factual site observations, and optional supporting material.\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n"
            "Professional review is required before real-world use.",
            reply_markup=_buttons(
                ("▶️ Start Pro Report", "pro:intro:start"),
                ("ℹ️ How It Works", "pro:intro:how"),
                ("✖️ Cancel", "pro:cancel"),
            ),
        )
        return INTRO

    async def begin_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query is None:
            return INTRO
        await query.answer()
        await query.edit_message_text(
            "Step 1 of 6 — Project information\n\n"
            "Send the project/report information you want identified in the draft. Include only what is relevant, for example:\n"
            "• project title/name\n"
            "• location\n"
            "• document/report title\n"
            "• client/company\n"
            "• inspection/date information\n\n"
            "Use /pro_cancel at any time to exit."
        )
        return PROJECT

    async def how_it_works(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query is None:
            return INTRO
        await query.answer()
        await query.edit_message_text(
            "🧰 BuildWise Pro — How It Works\n\n"
            "1️⃣ Project information\n"
            "2️⃣ Upload a PDF, PNG, JPG/JPEG drawing or sketch\n"
            "3️⃣ Add factual site notes\n"
            "4️⃣ Optionally upload a company template\n"
            "5️⃣ Optionally add a standard/code name for context\n"
            "6️⃣ Confirm and generate the draft\n\n"
            "You receive PDF + DOCX when available, with HTML fallback if document rendering fails.\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY — professional review is required before real-world use.",
            reply_markup=_buttons(
                ("▶️ Start Pro Report", "pro:intro:start"),
                ("✖️ Cancel", "pro:cancel"),
            ),
        )
        return INTRO
