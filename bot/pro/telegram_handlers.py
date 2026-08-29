"""Private Telegram beta workflow for BuildWise Pro professional draft reports."""

from __future__ import annotations

import asyncio
from datetime import date
from html import escape
from io import BytesIO
import logging
import re
from time import monotonic

from pydantic import ValidationError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import Settings
from bot.pro.ai_service import ProFile, ProfessionalReportService
from bot.pro.auth import PreviewUsageLimiter
from bot.pro.files import ProFileValidationError, detect_pro_file_type
from bot.pro.models import PROFESSIONAL_REVIEW_NOTICE, ProfessionalReport, ProfessionalReportRequest
from bot.services.openai_service import AIServiceError

logger = logging.getLogger(__name__)

PROJECT, DRAWING, NOTES, TEMPLATE, STANDARD, CONFIRM = range(6)
PRO_STATE_KEY = "buildwise_pro_report_workflow"


def _buttons(*rows: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=value)] for label, value in rows]
    )


def _safe_filename(value: str) -> str:
    first_line = value.strip().splitlines()[0] if value.strip() else "Project"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", first_line).strip("_")[:60]
    return cleaned or "Project"


def _list_html(items: list[str]) -> str:
    if not items:
        return "<p>Insufficient information provided.</p>"
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def render_report_html(report: ProfessionalReport, project_information: str) -> bytes:
    """Render a self-contained draft report without a new document-generation stack."""
    sections: list[tuple[str, str]] = []
    if report.template_structure_used and report.template_sections:
        sections.extend(
            (section.heading, _list_html(section.content)) for section in report.template_sections
        )
    else:
        sections.extend(
            [
                ("Project / document information", _list_html(report.project_document_information)),
                ("Executive summary", f"<p>{escape(report.summary)}</p>"),
                ("Observations", _list_html(report.observations)),
                ("Drawing / sketch findings", _list_html(report.drawing_sketch_findings)),
                ("Site-note findings", _list_html(report.site_note_findings)),
                ("Technical considerations", _list_html(report.relevant_technical_considerations)),
                ("Standard / code reference", _list_html(report.referenced_code_standard)),
                ("Recommendations", _list_html(report.recommendations)),
            ]
        )

    sections.extend(
        [
            ("Limitations / assumptions", _list_html(report.limitations_assumptions)),
            ("Uncertainties / missing information", _list_html(report.uncertainties_missing_information)),
            ("Professional review / sign-off", f"<p>{escape(report.review_signoff)}</p>"),
        ]
    )
    body = "".join(f"<section><h2>{escape(title)}</h2>{content}</section>" for title, content in sections)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>BuildWise Pro Draft Report</title>
<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;line-height:1.55;color:#171717}}h1,h2{{line-height:1.2}}.notice{{border:3px solid #9b1c1c;padding:16px;margin:20px 0;font-weight:700}}section{{margin:28px 0}}.meta{{color:#555}}</style></head>
<body><h1>BuildWise Pro — Draft Professional Report</h1>
<p class="meta">Project input: {escape(project_information[:2000])}</p>
<div class="notice">{escape(PROFESSIONAL_REVIEW_NOTICE)}</div>
{body}
<div class="notice">{escape(PROFESSIONAL_REVIEW_NOTICE)}</div>
</body></html>"""
    return html.encode("utf-8")


class TelegramProHandlers:
    """Conversation-scoped Pro beta handlers; student state stays namespaced and intact."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._allowed_ids = settings.buildwise_pro_allowed_telegram_ids
        self._service = ProfessionalReportService(settings)
        self._limiter = PreviewUsageLimiter()
        self._locks: dict[int, asyncio.Lock] = {}

    def register(self, application: Application) -> None:
        invalid = MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_step)
        cancel_button = CallbackQueryHandler(self.cancel_callback, pattern=r"^pro:cancel$")
        conversation = ConversationHandler(
            entry_points=[CommandHandler("pro", self.start)],
            states={
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
        # Give /pro_cancel a harmless response even when no Pro conversation is active.
        application.add_handler(CommandHandler("pro_cancel", self.cancel))

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        return bool(
            self._settings.buildwise_pro_enabled
            and self._allowed_ids
            and user is not None
            and user.id in self._allowed_ids
        )

    def _state(self, context: ContextTypes.DEFAULT_TYPE) -> dict[str, object]:
        state = context.user_data.get(PRO_STATE_KEY)
        if not isinstance(state, dict):
            state = {}
            context.user_data[PRO_STATE_KEY] = state
        return state

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
            "🧰 BUILDWISE PRO\nProfessional Report Generator\n\n"
            "Generate a structured draft professional report from project information, drawings/sketches, factual site observations, and optional supporting material.\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n"
            "Calculations, engineering conclusions, structural decisions, dimensions, loads, code/standards compliance, drawings, and safety-affecting recommendations require review by a suitably qualified professional.\n\n"
            "Step 1 of 6 — Project information\n"
            "Send the project/report information you want identified in the draft. Include only what is relevant, for example:\n"
            "• project title/name\n• location\n• document/report title\n• client/company\n• inspection/date information"
        )
        return PROJECT

    async def invalid_step(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "That input does not match the current BuildWise Pro step. Follow the prompt above or use /pro_cancel."
            )
        # Returning None keeps the current ConversationHandler state unchanged.
        return None

    async def project_information(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.effective_message.text or "").strip() if update.effective_message else ""
        if len(text) < 2 or len(text) > 2000:
            await update.effective_message.reply_text(
                "Send concise project information between 2 and 2,000 characters."
            )
            return PROJECT
        self._state(context)["project_information"] = text
        await update.effective_message.reply_text(
            "Step 2 of 6 — Drawing / sketch\n\nSend one PDF, PNG, JPG/JPEG drawing or sketch. BuildWise validates both the filename and file content before processing."
        )
        return DRAWING

    async def _download(self, context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
        telegram_file = await context.bot.get_file(file_id)
        return bytes(await telegram_file.download_as_bytearray())

    async def drawing(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        if message is None:
            return DRAWING
        if message.photo:
            photo = message.photo[-1]
            filename = "telegram-sketch.jpg"
            file_id = photo.file_id
            declared_size = photo.file_size
        elif message.document:
            filename = message.document.file_name or "drawing"
            file_id = message.document.file_id
            declared_size = message.document.file_size
        else:
            await message.reply_text("Send a PDF, PNG, JPG, or JPEG drawing/sketch.")
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
        logger.info(
            "BuildWise Pro Telegram drawing accepted user_id=%s type=%s size=%s",
            update.effective_user.id if update.effective_user else None,
            mime_type,
            len(data),
        )
        await message.reply_text(
            "Step 3 of 6 — Site notes\n\nSend the factual site observations or notes you want included in the report. BuildWise treats these as user-provided evidence, not independently verified facts."
        )
        return NOTES

    async def site_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.effective_message.text or "").strip() if update.effective_message else ""
        if len(text) < 3:
            await update.effective_message.reply_text(
                "Please provide factual site observations or notes before continuing."
            )
            return NOTES
        if len(text) > 16000:
            await update.effective_message.reply_text(
                "Site notes are too long for this beta. Keep them under 16,000 characters."
            )
            return NOTES
        self._state(context)["site_notes"] = text
        await update.effective_message.reply_text(
            "Step 4 of 6 — Company template\n\nUpload an optional PDF, TXT, or Markdown company report template, or continue without one.",
            reply_markup=_buttons(
                ("Continue without template", "pro:template:skip"),
                ("Cancel", "pro:cancel"),
            ),
        )
        return TEMPLATE

    async def template_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        self._state(context)["template"] = None
        await update.callback_query.edit_message_text(
            "Step 5 of 6 — Engineering standard / code reference\n\nType an optional reference such as BS 8110, Eurocode 2, or ACI 318. A name is contextual information only; BuildWise will not invent clauses, editions, equations, limits, citations, or compliance results.",
            reply_markup=_buttons(
                ("Continue without standard reference", "pro:standard:skip"),
                ("Cancel", "pro:cancel"),
            ),
        )
        return STANDARD

    async def template(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        message = update.effective_message
        if message is None or message.document is None:
            return TEMPLATE
        document = message.document
        filename = document.file_name or "template"
        max_bytes = self._settings.buildwise_pro_max_file_size_bytes
        if document.file_size is not None and document.file_size > max_bytes:
            await message.reply_text(
                f"That template is too large. Maximum: {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return TEMPLATE
        data = await self._download(context, document.file_id)
        if len(data) > max_bytes:
            await message.reply_text(
                f"That template is too large. Maximum: {self._settings.buildwise_pro_max_file_size_mb} MB."
            )
            return TEMPLATE
        try:
            mime_type = detect_pro_file_type(filename, data, template=True)
        except ProFileValidationError as exc:
            await message.reply_text(str(exc))
            return TEMPLATE
        self._state(context)["template"] = ProFile(
            filename=filename[:180], mime_type=mime_type, data=data
        )
        logger.info(
            "BuildWise Pro Telegram template accepted user_id=%s type=%s size=%s",
            update.effective_user.id if update.effective_user else None,
            mime_type,
            len(data),
        )
        await message.reply_text(
            "Step 5 of 6 — Engineering standard / code reference\n\nType an optional reference such as BS 8110, Eurocode 2, or ACI 318. BuildWise will treat the name as context only unless permitted source text is actually supplied.",
            reply_markup=_buttons(
                ("Continue without standard reference", "pro:standard:skip"),
                ("Cancel", "pro:cancel"),
            ),
        )
        return STANDARD

    async def standard_reference(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = (update.effective_message.text or "").strip() if update.effective_message else ""
        if len(text) > 1200:
            await update.effective_message.reply_text(
                "Keep the standard/code reference under 1,200 characters."
            )
            return STANDARD
        self._state(context)["standard_reference"] = text
        return await self._confirmation(update, context)

    async def standard_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        self._state(context)["standard_reference"] = ""
        return await self._confirmation(update, context, edit=True)

    async def _confirmation(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        edit: bool = False,
    ) -> int:
        state = self._state(context)
        drawing = state.get("drawing")
        template = state.get("template")
        filename = getattr(drawing, "filename", "missing")
        text = (
            "Step 6 of 6 — Confirm draft generation\n\n"
            f"Project information: {'received' if state.get('project_information') else 'missing'}\n"
            f"Drawing/sketch: {filename}\n"
            f"Site notes: {'received' if state.get('site_notes') else 'missing'}\n"
            f"Company template: {'yes' if template else 'no'}\n"
            f"Standard/code reference: {'yes' if state.get('standard_reference') else 'no'}\n\n"
            "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\nGenerate only after confirming these inputs are correct."
        )
        markup = _buttons(
            ("Generate Draft Report", "pro:generate"),
            ("Cancel", "pro:cancel"),
        )
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=markup)
        return CONFIRM

    async def generate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        user = update.effective_user
        if user is None or not self._authorized(update):
            await query.edit_message_text(
                "BuildWise Pro beta access is not authorized for this account."
            )
            context.user_data.pop(PRO_STATE_KEY, None)
            return ConversationHandler.END
        lock = self._locks.setdefault(user.id, asyncio.Lock())
        if lock.locked():
            await query.edit_message_text(
                "A BuildWise Pro report is already being generated for this account."
            )
            return CONFIRM
        state = self._state(context)
        drawing = state.get("drawing")
        if not isinstance(drawing, ProFile) or not state.get("site_notes"):
            await query.edit_message_text(
                "The Pro workflow is incomplete. Start again with /pro."
            )
            context.user_data.pop(PRO_STATE_KEY, None)
            return ConversationHandler.END

        async with lock:
            usage_date = date.today()
            allowed, used = await self._limiter.reserve(
                str(user.id), usage_date, self._settings.buildwise_pro_daily_request_limit
            )
            if not allowed:
                await query.edit_message_text(
                    "BuildWise Pro beta daily report limit reached. Try again after the daily reset."
                )
                return CONFIRM
            started = monotonic()
            await query.edit_message_text("BuildWise Pro is generating your draft report…")
            if update.effective_chat:
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            try:
                request = ProfessionalReportRequest(
                    project_information=str(state.get("project_information") or ""),
                    site_notes=str(state.get("site_notes") or ""),
                    standard_reference=str(state.get("standard_reference") or ""),
                )
                template = state.get("template")
                report = await self._service.generate_professional_report(
                    request=request,
                    drawing=drawing,
                    template=template if isinstance(template, ProFile) else None,
                )
            except (AIServiceError, ValidationError) as exc:
                await self._limiter.refund(str(user.id), usage_date)
                public_message = (
                    exc.public_message
                    if isinstance(exc, AIServiceError)
                    else "The report inputs could not be validated. Start again with /pro."
                )
                logger.warning(
                    "BuildWise Pro Telegram generation failed user_id=%s", user.id
                )
                await query.edit_message_text(public_message)
                context.user_data.pop(PRO_STATE_KEY, None)
                return ConversationHandler.END

            project = str(state.get("project_information") or "Project")
            html = render_report_html(report, project)
            filename = f"BuildWise_Pro_Draft_Report_{_safe_filename(project)}.html"
            summary = report.summary.strip()
            if len(summary) > 900:
                summary = summary[:897].rstrip() + "…"
            await query.edit_message_text(
                "✅ BuildWise Pro draft complete\n\n"
                "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n\n"
                f"Summary: {summary}\n\n"
                "The downloadable report includes limitations, uncertainties/missing information, and a professional review/sign-off section."
            )
            if update.effective_chat:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=InputFile(BytesIO(html), filename=filename),
                    caption=(
                        "BuildWise Pro draft report — professional review required before real-world use."
                    ),
                )
            logger.info(
                "BuildWise Pro Telegram report generated user_id=%s drawing_type=%s template=%s standard_ref=%s usage=%s/%s latency_ms=%s",
                user.id,
                drawing.mime_type,
                isinstance(state.get("template"), ProFile),
                bool(state.get("standard_reference")),
                used,
                self._settings.buildwise_pro_daily_request_limit,
                round((monotonic() - started) * 1000),
            )
            context.user_data.pop(PRO_STATE_KEY, None)
            return ConversationHandler.END

    async def cancel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        context.user_data.pop(PRO_STATE_KEY, None)
        await update.callback_query.edit_message_text(
            "BuildWise Pro report workflow cancelled. Your student BuildWise state is unchanged."
        )
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.pop(PRO_STATE_KEY, None)
        if update.effective_message:
            await update.effective_message.reply_text(
                "BuildWise Pro report workflow cancelled. Your student BuildWise state is unchanged."
            )
        return ConversationHandler.END
