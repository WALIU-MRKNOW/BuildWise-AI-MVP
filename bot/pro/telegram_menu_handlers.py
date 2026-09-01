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

from bot.pro.ai_service import ProFile
from bot.pro.files import ProFileValidationError, detect_pro_file_type
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
_SITE_NOTE_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


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
                    MessageHandler(filters.Document.ALL, self.site_notes_file),
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
            "Analyze project information, floor plans, architectural/engineering drawings, sketches, project documents and site-note files, then turn the supplied evidence into a structured draft professional report.\n\n"
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
            "B) Upload your primary project evidence now. This can be a floor plan, architectural/engineering drawing, sketch, or project document in PDF, DOCX, TXT, PNG or JPG/JPEG format. BuildWise Pro will treat it as a source to ANALYZE — not merely read — and will extract only details that are explicitly supported by the file.\n\n"
            "If you upload the project source here, BuildWise will use it as the primary project document and move directly to factual site notes.\n\n"
            "Use /pro_cancel at any time to exit."
        )
        return PROJECT

    async def project_source_direct(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Accept a project source at step one without requiring duplicate typed metadata."""
        message = update.effective_message
        if message is None:
            return PROJECT

        result = await self.drawing(update, context)
        if result != NOTES:
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

    async def drawing(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Validate and retain one Pro source, then explicitly route it to report analysis."""
        message = update.effective_message
        if message is None:
            return DRAWING

        if message.photo:
            photo = message.photo[-1]
            filename = "telegram-project-image.jpg"
            file_id = photo.file_id
            declared_size = photo.file_size
        elif message.document:
            filename = message.document.file_name or "project-source"
            file_id = message.document.file_id
            declared_size = message.document.file_size
        else:
            await message.reply_text(
                "Upload a floor plan, architectural/engineering drawing, sketch, or project document in PDF, DOCX, TXT, PNG, JPG or JPEG format for analysis."
            )
            return DRAWING

        max_bytes = self._settings.buildwise_pro_max_file_size_bytes
        if declared_size is not None and declared_size > max_bytes:
            await message.reply_text(
                f"That file is too large. BuildWise Pro allows up to {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return DRAWING

        data = await self._download(context, file_id)
        if len(data) > max_bytes:
            await message.reply_text(
                f"That file is too large. BuildWise Pro allows up to {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return DRAWING

        try:
            mime_type = detect_pro_file_type(filename, data)
        except ProFileValidationError as exc:
            await message.reply_text(str(exc))
            return DRAWING

        self._state(context)["drawing"] = ProFile(
            filename=filename[:180], mime_type=mime_type, data=data
        )
        await message.reply_text(
            "✅ Project source / floor plan accepted for analysis.\n\n"
            "BuildWise Pro will analyze the supplied evidence for explicit project/document details, headings and structure, tables or listed data, visible/legible floor-plan or drawing information, observations, stated recommendations, contradictions, gaps and uncertainties. It will not invent missing engineering facts or treat uploaded claims as independently verified.\n\n"
            "Step 3 of 6 — Site notes\n\n"
            "Type factual site observations OR upload one PDF, DOCX or TXT site-notes file. BuildWise will analyze uploaded site notes alongside the project source as user-provided evidence, not independently verified facts."
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
                "Step 2 of 6 — Floor plan / drawing / project document\n\n"
                "Upload your primary project evidence: a floor plan, architectural/engineering drawing, sketch, or project document. Supported formats are PDF, DOCX, TXT, PNG and JPG/JPEG. "
                "BuildWise will validate it and analyze the supplied content as evidence for the draft report."
            )
        return DRAWING

    async def _prompt_template(self, message) -> None:
        await message.reply_text(
            "Step 4 of 6 — Company template\n\n"
            "Upload an optional PDF, DOCX, TXT, or Markdown company report template, or continue without one.",
            reply_markup=_buttons(
                ("Continue without template", "pro:template:skip"),
                ("Cancel", "pro:cancel"),
            ),
        )

    async def site_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        if len(text) < 3:
            if message:
                await message.reply_text(
                    "Please type factual site observations or upload a PDF, DOCX or TXT site-notes file."
                )
            return NOTES
        if len(text) > 16000:
            if message:
                await message.reply_text(
                    "Site notes are too long for this beta. Keep typed notes under 16,000 characters or upload a supported site-notes file."
                )
            return NOTES
        self._state(context)["site_notes"] = text
        if message:
            await self._prompt_template(message)
        return TEMPLATE

    async def site_notes_file(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Accept PDF/DOCX/TXT site notes as a secondary Pro analysis source."""
        message = update.effective_message
        if message is None or message.document is None:
            return NOTES

        document = message.document
        filename = document.file_name or "site-notes"
        max_bytes = self._settings.buildwise_pro_max_file_size_bytes
        if document.file_size is not None and document.file_size > max_bytes:
            await message.reply_text(
                f"That site-notes file is too large. BuildWise Pro allows up to {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return NOTES

        data = await self._download(context, document.file_id)
        if len(data) > max_bytes:
            await message.reply_text(
                f"That site-notes file is too large. BuildWise Pro allows up to {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return NOTES

        try:
            mime_type = detect_pro_file_type(filename, data)
        except ProFileValidationError as exc:
            await message.reply_text(str(exc))
            return NOTES

        if mime_type not in _SITE_NOTE_MIME_TYPES:
            await message.reply_text(
                "Site notes must be uploaded as PDF, DOCX or TXT. Images, floor plans and drawings remain supported as the primary project source."
            )
            return NOTES

        state = self._state(context)
        primary_source = state.get("drawing")
        if not isinstance(primary_source, ProFile):
            await message.reply_text(
                "The primary project source is missing. Start the Pro workflow again with /pro."
            )
            return NOTES

        site_notes_source = ProFile(
            filename=filename[:180], mime_type=mime_type, data=data
        )
        state["drawing"] = ProFile(
            filename=primary_source.filename,
            mime_type=primary_source.mime_type,
            data=primary_source.data,
            attachments=primary_source.attachments + (site_notes_source,),
        )
        state["site_notes"] = (
            f"Site observations are supplied in uploaded file '{filename[:180]}'. Analyze that attached "
            "site-notes source as user-provided evidence alongside the primary project source. Do not "
            "treat its claims as independently verified and do not invent missing observations."
        )

        await message.reply_text(
            "✅ Site notes file accepted for analysis.\n\n"
            "BuildWise Pro will analyze its explicit observations, headings, tables/listed data, stated concerns, actions, contradictions, gaps and uncertainties alongside the primary project source. It will not merely read the file back or treat its claims as independently verified."
        )
        await self._prompt_template(message)
        return TEMPLATE

    async def how_it_works(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query is None:
            return INTRO
        await query.answer()
        await query.edit_message_text(
            "🧰 BuildWise Pro — How It Works\n\n"
            "1️⃣ Type project information OR upload your primary project evidence for analysis\n"
            "2️⃣ Primary evidence can be a floor plan, architectural/engineering drawing, sketch, or project document (PDF, DOCX, TXT, PNG, JPG/JPEG)\n"
            "3️⃣ Type site notes OR upload a PDF, DOCX or TXT site-notes file for analysis\n"
            "4️⃣ Optionally upload a company template (PDF, DOCX, TXT, or Markdown)\n"
            "5️⃣ Optionally add a standard/code name for context\n"
            "6️⃣ Confirm and generate the draft\n\n"
            "BuildWise analyzes supplied source content for explicit project facts, visible/legible floor-plan or drawing information, observations, document structure, tables, recommendations, gaps and uncertainties. It must not invent unsupported engineering information.\n\n"
            "You receive PDF + DOCX when available, with HTML fallback if document rendering fails.\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY — professional review is required before real-world use.",
            reply_markup=_buttons(
                ("▶️ Start Pro Report", "pro:intro:start"),
                ("✖️ Cancel", "pro:cancel"),
            ),
        )
        return INTRO
