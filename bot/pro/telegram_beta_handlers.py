"""BuildWise Pro Beta 1.1 Telegram measurement, status, feedback, and admin controls."""

from __future__ import annotations

import asyncio
from datetime import date
from io import BytesIO
import logging
from time import monotonic
from uuid import UUID, uuid4

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

from bot.admin import admin_configuration_error
from bot.pro.ai_service import ProFile
from bot.pro.beta_analytics import ProBetaAnalyticsError, ProBetaAnalyticsService
from bot.pro.beta_config import (
    PRO_BETA_MAX_TESTERS,
    validated_admin_telegram_ids,
    validated_pro_allowlist,
)
from bot.pro.models import ProfessionalReportRequest
from bot.pro.report_output import render_report_docx, render_report_html, render_report_pdf
from bot.pro.telegram_handlers import CONFIRM, PRO_STATE_KEY, _buttons, _safe_filename
from bot.pro.telegram_menu_handlers import TelegramProMenuHandlers
from bot.services.openai_service import AIServiceError

logger = logging.getLogger(__name__)

PRO_SESSION_ID_KEY = "buildwise_pro_session_id"
PRO_FEEDBACK_KEY = "buildwise_pro_feedback_session"
FEEDBACK_TEXT = 100


def _feedback_markup(session_id: UUID) -> InlineKeyboardMarkup:
    sid = str(session_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👍 Helpful", callback_data=f"pro:fb:h:{sid}"),
                InlineKeyboardButton("👎 Needs improvement", callback_data=f"pro:fb:n:{sid}"),
            ],
            [InlineKeyboardButton("💬 Send feedback", callback_data=f"pro:fb:t:{sid}")],
        ]
    )


class TelegramProBetaHandlers(TelegramProMenuHandlers):
    """Private Pro beta controls layered on the existing report workflow."""

    def __init__(self, settings) -> None:
        # Hard validation runs at startup before Telegram handlers are registered.
        allowlist = validated_pro_allowlist(settings)
        super().__init__(settings)
        self._allowed_ids = allowlist
        self._admin_telegram_ids = validated_admin_telegram_ids()
        self._analytics = ProBetaAnalyticsService(settings)

        logger.info(
            "BuildWise Pro Beta 1.1 configured testers=%s/%s admin_telegram_bindings=%s",
            len(self._allowed_ids),
            PRO_BETA_MAX_TESTERS,
            len(self._admin_telegram_ids),
        )

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("myid", self.myid))
        application.add_handler(CommandHandler("pro_status", self.pro_status))
        application.add_handler(CommandHandler("pro_beta_stats", self.pro_beta_stats))
        super().register(application)

        application.add_handler(
            CallbackQueryHandler(self.feedback_rating, pattern=r"^pro:fb:[hn]:[0-9a-f-]{36}$")
        )
        feedback_conversation = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    self.feedback_text_start, pattern=r"^pro:fb:t:[0-9a-f-]{36}$"
                )
            ],
            states={
                FEEDBACK_TEXT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.feedback_text_submit)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.feedback_cancel)],
            allow_reentry=True,
            name="buildwise-pro-feedback-beta",
            persistent=False,
        )
        application.add_handler(feedback_conversation)

    async def myid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        lines = [f"Your Telegram ID: {user.id}"]
        if user.username:
            lines.append(f"Username: @{user.username}")
        if user.first_name:
            lines.append(f"First name: {user.first_name}")
        await message.reply_text("\n".join(lines))

    async def pro_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if not self._settings.buildwise_pro_enabled or user.id not in self._allowed_ids:
            await message.reply_text(
                "BuildWise Pro is currently a limited private beta. This account is not currently allowlisted."
            )
            return
        used = await self._limiter.current(str(user.id), date.today())
        await message.reply_text(
            "BuildWise Pro Beta access: Active\n"
            f"Reports used today: {used} / {self._settings.buildwise_pro_daily_request_limit}\n"
            "Supported outputs: PDF + DOCX\n\n"
            "Privacy: BuildWise Pro beta analytics stores operational metadata only, not drawings, site notes, templates, or report bodies.\n\n"
            "Send /pro to start a report."
        )

    async def begin_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        session_id = uuid4()
        self._state(context)[PRO_SESSION_ID_KEY] = str(session_id)
        if user is not None:
            try:
                await self._analytics.record_started(session_id=session_id, telegram_id=user.id)
            except ProBetaAnalyticsError:
                logger.warning("BuildWise Pro beta start metric unavailable user_id=%s", user.id)
        return await super().begin_report(update, context)

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

        raw_session_id = str(state.get(PRO_SESSION_ID_KEY) or "")
        try:
            session_id = UUID(raw_session_id)
        except ValueError:
            session_id = uuid4()
            state[PRO_SESSION_ID_KEY] = str(session_id)
            try:
                await self._analytics.record_started(session_id=session_id, telegram_id=user.id)
            except ProBetaAnalyticsError:
                logger.warning("BuildWise Pro beta recovery start metric unavailable user_id=%s", user.id)

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
                latency_ms = round((monotonic() - started) * 1000)
                await self._limiter.refund(str(user.id), usage_date)
                try:
                    await self._analytics.record_generation(
                        session_id=session_id,
                        status="failed",
                        latency_ms=latency_ms,
                    )
                except ProBetaAnalyticsError:
                    logger.warning("BuildWise Pro beta failure metric unavailable user_id=%s", user.id)
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

            outputs: list[tuple[bytes, str, str]] = []
            pdf_success = False
            docx_success = False
            render_errors: list[str] = []
            try:
                outputs.append((render_report_pdf(report, project), pdf_name, "PDF"))
                pdf_success = True
            except Exception:
                logger.exception("BuildWise Pro PDF rendering failed user_id=%s", user.id)
                render_errors.append("PDF")
            try:
                outputs.append((render_report_docx(report, project), docx_name, "DOCX"))
                docx_success = True
            except Exception:
                logger.exception("BuildWise Pro DOCX rendering failed user_id=%s", user.id)
                render_errors.append("DOCX")
            if render_errors:
                outputs.append((render_report_html(report, project), html_name, "HTML fallback"))

            latency_ms = round((monotonic() - started) * 1000)
            try:
                await self._analytics.record_generation(
                    session_id=session_id,
                    status="success",
                    latency_ms=latency_ms,
                    pdf_success=pdf_success,
                    docx_success=docx_success,
                )
            except ProBetaAnalyticsError:
                logger.warning("BuildWise Pro beta success metric unavailable user_id=%s", user.id)

            summary = report.summary.strip()
            if len(summary) > 900:
                summary = summary[:897].rstrip() + "…"
            available = " + ".join(label for _, _, label in outputs)
            await query.edit_message_text(
                "✅ BuildWise Pro draft complete\n\n"
                "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n\n"
                f"Summary: {summary}\n\n"
                f"Files: {available}\n"
                "Every output includes limitations, uncertainties/missing information, and a professional review/sign-off section.\n\n"
                "How was this draft?",
                reply_markup=_feedback_markup(session_id),
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
                "BuildWise Pro Telegram report generated user_id=%s drawing_type=%s template=%s standard_ref=%s pdf_success=%s docx_success=%s usage=%s/%s latency_ms=%s",
                user.id,
                drawing.mime_type,
                isinstance(state.get("template"), ProFile),
                bool(state.get("standard_reference")),
                pdf_success,
                docx_success,
                used,
                self._settings.buildwise_pro_daily_request_limit,
                latency_ms,
            )
            context.user_data.pop(PRO_STATE_KEY, None)
            return ConversationHandler.END

    async def feedback_rating(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return
        await query.answer()
        if user.id not in self._allowed_ids:
            await query.answer("BuildWise Pro beta access required.", show_alert=True)
            return
        parts = (query.data or "").split(":")
        try:
            session_id = UUID(parts[3])
        except (IndexError, ValueError):
            await query.answer("Feedback reference is invalid.", show_alert=True)
            return
        rating = "helpful" if parts[2] == "h" else "needs_improvement"
        try:
            await self._analytics.add_rating(
                session_id=session_id, telegram_id=user.id, rating=rating
            )
        except (ProBetaAnalyticsError, ValueError):
            logger.warning("BuildWise Pro beta rating unavailable user_id=%s", user.id)
            await query.answer("Could not save feedback right now.", show_alert=True)
            return
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("💬 Send feedback", callback_data=f"pro:fb:t:{session_id}")]]
            )
        )
        await query.answer("Thanks — feedback saved.")

    async def feedback_text_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return ConversationHandler.END
        await query.answer()
        if user.id not in self._allowed_ids:
            await query.answer("BuildWise Pro beta access required.", show_alert=True)
            return ConversationHandler.END
        parts = (query.data or "").split(":")
        try:
            session_id = UUID(parts[3])
        except (IndexError, ValueError):
            await query.answer("Feedback reference is invalid.", show_alert=True)
            return ConversationHandler.END
        context.user_data[PRO_FEEDBACK_KEY] = str(session_id)
        await query.message.reply_text(
            "Send a short BuildWise Pro beta comment (max 1,500 characters).\n\n"
            "Please do NOT paste confidential engineering/project information, drawings, site notes, report text, or secrets into feedback.\n\n"
            "Send /cancel to stop."
        )
        return FEEDBACK_TEXT

    async def feedback_text_submit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return ConversationHandler.END
        raw_session_id = str(context.user_data.get(PRO_FEEDBACK_KEY) or "")
        try:
            session_id = UUID(raw_session_id)
        except ValueError:
            context.user_data.pop(PRO_FEEDBACK_KEY, None)
            await message.reply_text("That feedback session expired. Generate another Pro report first.")
            return ConversationHandler.END
        text = (message.text or "").strip()
        if not 1 <= len(text) <= 1500:
            await message.reply_text("Keep feedback between 1 and 1,500 characters.")
            return FEEDBACK_TEXT
        try:
            await self._analytics.add_text_feedback(
                session_id=session_id, telegram_id=user.id, text=text
            )
        except (ProBetaAnalyticsError, ValueError):
            logger.warning("BuildWise Pro beta text feedback unavailable user_id=%s", user.id)
            await message.reply_text("Could not save feedback right now. Please try again later.")
            context.user_data.pop(PRO_FEEDBACK_KEY, None)
            return ConversationHandler.END
        context.user_data.pop(PRO_FEEDBACK_KEY, None)
        await message.reply_text("Thanks — your BuildWise Pro beta feedback was saved.")
        return ConversationHandler.END

    async def feedback_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.pop(PRO_FEEDBACK_KEY, None)
        if update.effective_message:
            await update.effective_message.reply_text("Feedback cancelled.")
        return ConversationHandler.END

    async def pro_beta_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if admin_configuration_error(self._settings) is not None or user.id not in self._admin_telegram_ids:
            await message.reply_text("Admin authorization required.")
            return
        try:
            stats = await self._analytics.summary()
        except ProBetaAnalyticsError:
            logger.warning("BuildWise Pro beta admin stats unavailable user_id=%s", user.id)
            await message.reply_text("BuildWise Pro beta stats are temporarily unavailable.")
            return

        def pct(value) -> str:
            return "n/a" if value is None else f"{value * 100:.1f}%"

        latency = stats["average_generation_latency_ms"]
        latency_text = "n/a" if latency is None else f"{latency} ms"
        await message.reply_text(
            "BuildWise Pro Beta 1.1 — Private Stats\n\n"
            f"Allowlisted testers: {len(self._allowed_ids)} / {PRO_BETA_MAX_TESTERS}\n"
            f"Testers who used Pro: {stats['testers_used']}\n"
            f"Reports started: {stats['reports_started']}\n"
            f"Reports completed: {stats['reports_completed']}\n"
            f"Generation failures: {stats['generation_failures']}\n"
            f"Average generation latency: {latency_text}\n"
            f"PDF success rate: {pct(stats['pdf_success_rate'])}\n"
            f"DOCX success rate: {pct(stats['docx_success_rate'])}\n"
            f"👍 Helpful: {stats['helpful_count']}\n"
            f"👎 Needs improvement: {stats['needs_improvement_count']}\n"
            f"Feedback rate: {pct(stats['feedback_rate'])}\n"
            f"Token usage: {'available' if stats['token_usage_available'] else 'unavailable'}\n"
            "Approximate AI cost: unavailable (pricing calculation not implemented reliably)"
        )
