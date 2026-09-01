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
                    MessageHandler(filters.PHOTO | filters.Document.ALL, self.project_source_direct),
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
            "Analyze project information, drawings/sketches or project documents and turn the supplied evidence into a structured draft professional report.\n\n"
            "Uploads supported in Pro: PDF, DOCX, TXT, PNG, JPG/JPEG.\n\n"
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
            "Step 1 of 6 — Project information / project source\n\n"
            "Choose either option:\n\n"
            "A) Type the project/report information, such as project name, location, report title, client/company and date.\n\n"
            "B) Upload a PDF, DOCX, TXT, PNG or JPG/JPEG project document now. BuildWise Pro will treat it as a source to ANALYZE — not merely read — and will extract only details that are explicitly supported by the file.\n\n"
            "If you upload the project source here, BuildWise will use it as the primary project document and move directly to factual site notes.\n\n"
            "Use /pro_cancel at any time to exit."
        )
        return PROJECT

    async def project_source_direct(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Accept a project source at step one and analyze it without requiring duplicate typing."""
        message = update.effective_message
        if message is None:
            return PROJECT

        result = await self.drawing(update, context)
        if result != NOTES:
            # Base validation returns DRAWING on an invalid upload. Keep the user at
            # step one because they chose the direct project-source path.
            return PROJECT

        filename = "uploaded project source"
        if message.document and message.document.file_name:
            filename = message.document.file_name[:180]
        elif message.photo:
            filename = "Telegram project image"

        self._state(context)["project_information"] = (
            f"No separate project information was typed. Analyze '{filename}' and extract only "
            "project/document identifiers that are explicitly present in the uploaded source. "
            "Do not infer missing names, dates, locations, parties, dimensions, or engineering facts."
        )
        return NOTES

    async def project_information(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        if len(text) < 2 or len(text) > 2000:
            if message:
                await message.reply_text(
                    "Send concise project information between 2 and 2,000 characters."
                )
            return PROJECT
        self._state(context)["project_information"] = text
        if message:
            await message.reply_text(
                "Step 2 of 6 — Drawing / project document\n\n"
                "Upload one PDF, DOCX, TXT, PNG, JPG/JPEG drawing, sketch, or project document. "
                "BuildWise will validate it and analyze the supplied content as evidence for the draft report."
            )
        return DRAWING

    async def site_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        if len(text) < 3:
            if message:
                await message.reply_text(
                    "Please provide factual site observations or notes before continuing."
                )
            return NOTES
        if len(text) > 16000:
            if message:
                await message.reply_text(
                    "Site notes are too long for this beta. Keep them under 16,000 characters."
                )
            return NOTES
        self._state(context)["site_notes"] = text
        if message:
            await message.reply_text(
                "Step 4 of 6 — Company template\n\n"
                "Upload an optional PDF, DOCX, TXT, or Markdown company report template, or continue without one.",
                reply_markup=_buttons(
                    ("Continue without template", "pro:template:skip"),
                    ("Cancel", "pro:cancel"),
                ),
            )
        return TEMPLATE

    async def how_it_works(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query is None:
            return INTRO
        await query.answer()
        await query.edit_message_text(
            "🧰 BuildWise Pro — How It Works\n\n"
            "1️⃣ Type project information OR upload a project source for analysis\n"
            "2️⃣ If you typed project information, upload a PDF, DOCX, TXT, PNG, JPG/JPEG source\n"
            "3️⃣ Add factual site notes\n"
            "4️⃣ Optionally upload a company template (PDF, DOCX, TXT, or Markdown)\n"
            "5️⃣ Optionally add a standard/code name for context\n"
            "6️⃣ Confirm and generate the draft\n\n"
            "BuildWise analyzes supplied source content for explicit project facts, observations, document structure, tables, recommendations, gaps and uncertainties. It must not invent unsupported engineering information.\n\n"
            "You receive PDF + DOCX when available, with HTML fallback if document rendering fails.\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY — professional review is required before real-world use.",
            reply_markup=_buttons(
                ("▶️ Start Pro Report", "pro:intro:start"),
                ("✖️ Cancel", "pro:cancel"),
            ),
        )
        return INTRO
