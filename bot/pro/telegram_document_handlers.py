"""Telegram BuildWise Pro handler with PDF + DOCX report delivery."""

from __future__ import annotations

import asyncio
from datetime import date
from io import BytesIO
import logging
from time import monotonic

from pydantic import ValidationError
from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, ConversationHandler

from bot.pro.ai_service import ProFile
from bot.pro.report_output import render_report_docx, render_report_html, render_report_pdf
from bot.pro.models import ProfessionalReportRequest
from bot.pro.telegram_handlers import CONFIRM, PRO_STATE_KEY, TelegramProHandlers, _safe_filename
from bot.services.openai_service import AIServiceError

logger = logging.getLogger(__name__)


class TelegramProDocumentHandlers(TelegramProHandlers):
    """Keep the existing Pro workflow, but deliver PDF and DOCX draft files."""

    async def generate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query is None:
            return ConversationHandler.END
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
                logger.warning("BuildWise Pro Telegram generation failed user_id=%s", user.id)
                await query.edit_message_text(public_message)
                context.user_data.pop(PRO_STATE_KEY, None)
                return ConversationHandler.END

            project = str(state.get("project_information") or "Project")
            safe_name = _safe_filename(project)
            pdf_name = f"BuildWise_Pro_Draft_Report_{safe_name}.pdf"
            docx_name = f"BuildWise_Pro_Draft_Report_{safe_name}.docx"
            html_name = f"BuildWise_Pro_Draft_Report_{safe_name}.html"

            # Render formats independently. If either office-document renderer fails, keep the
            # already-safe HTML representation as a recovery output rather than losing the report.
            outputs: list[tuple[bytes, str, str]] = []
            render_errors: list[str] = []
            try:
                outputs.append((render_report_pdf(report, project), pdf_name, "PDF"))
            except Exception:
                logger.exception("BuildWise Pro PDF rendering failed user_id=%s", user.id)
                render_errors.append("PDF")
            try:
                outputs.append((render_report_docx(report, project), docx_name, "DOCX"))
            except Exception:
                logger.exception("BuildWise Pro DOCX rendering failed user_id=%s", user.id)
                render_errors.append("DOCX")
            if render_errors:
                outputs.append((render_report_html(report, project), html_name, "HTML fallback"))

            if not outputs:
                await self._limiter.refund(str(user.id), usage_date)
                await query.edit_message_text(
                    "The draft was generated, but BuildWise could not create a downloadable file. "
                    "Your Pro request was refunded; please try again."
                )
                context.user_data.pop(PRO_STATE_KEY, None)
                return ConversationHandler.END

            summary = report.summary.strip()
            if len(summary) > 900:
                summary = summary[:897].rstrip() + "…"
            available = " + ".join(label for _, _, label in outputs)
            await query.edit_message_text(
                "✅ BuildWise Pro draft complete\n\n"
                "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n\n"
                f"Summary: {summary}\n\n"
                f"Files: {available}\n"
                "Every output includes limitations, uncertainties/missing information, and a professional review/sign-off section."
            )

            if update.effective_chat:
                for data, filename, label in outputs:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=InputFile(BytesIO(data), filename=filename),
                        caption=(
                            f"BuildWise Pro {label} draft — professional review required before real-world use."
                        ),
                    )

            logger.info(
                "BuildWise Pro Telegram report generated user_id=%s drawing_type=%s template=%s standard_ref=%s outputs=%s usage=%s/%s latency_ms=%s",
                user.id,
                drawing.mime_type,
                isinstance(state.get("template"), ProFile),
                bool(state.get("standard_reference")),
                [label for _, _, label in outputs],
                used,
                self._settings.buildwise_pro_daily_request_limit,
                round((monotonic() - started) * 1000),
            )
            context.user_data.pop(PRO_STATE_KEY, None)
            return ConversationHandler.END
