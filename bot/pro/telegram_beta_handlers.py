"""BuildWise Pro Beta 1.1 measurement, feedback, status, and beta-control handlers."""

from __future__ import annotations

import asyncio
from datetime import date
from io import BytesIO
import logging
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

from bot.admin import admin_configuration_error
from bot.pro.ai_service import ProFile
from bot.pro.beta_analytics import ProBetaAnalyticsError, ProBetaAnalyticsService
from bot.pro.models import ProfessionalReportRequest
from bot.pro.report_output import render_report_docx, render_report_html, render_report_pdf
from bot.pro.telegram_handlers import CONFIRM, PRO_STATE_KEY, _buttons, _safe_filename
from bot.pro.telegram_menu_handlers import TelegramProMenuHandlers
from bot.services.openai_service import AIServiceError

logger = logging.getLogger(__name__)
FEEDBACK_TEXT = 1
FEEDBACK_SESSION_KEY = "buildwise_pro_feedback_session"


def _feedback_buttons(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👍 Helpful", callback_data=f"pro:fb:up:{session_id}"),
                InlineKeyboardButton(
                    "👎 Needs improvement", callback_data=f"pro:fb:down:{session_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "💬 Send feedback", callback_data=f"pro:fb:text:{session_id}"
                )
            ],
        ]
    )


class TelegramProBetaHandlers(TelegramProMenuHandlers):
    """Keep Pro drafting intact while adding metadata-only beta measurement."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._analytics = ProBetaAnalyticsService(settings)

    def register(self, application: Application) -> None:
        super().register(application)
        application.add_handler(CommandHandler("myid", self.myid))
        application.add_handler(CommandHandler("pro_status", self.pro_status))
        application.add_handler(CommandHandler("pro_beta_stats", self.pro_beta_stats))
        application.add_handler(
            CallbackQueryHandler(self.feedback_rating, pattern=r"^pro:fb:(up|down):[0-9a-f-]{36}$")
        )
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CallbackQueryHandler(
                        self.feedback_comment_prompt,
                        pattern=r"^pro:fb:text:[0-9a-f-]{36}$",
                    )
                ],
                states={
                    FEEDBACK_TEXT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.feedback_comment)
                    ]
                },
                fallbacks=[CommandHandler("cancel", self.feedback_cancel)],
                name="buildwise-pro-beta-feedback",
                persistent=False,
            )
        )

    async def myid(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        lines = [f"Your Telegram ID: {user.id}"]
        if user.username:
            lines.append(f"Username: @{user.username}")
        if user.first_name:
            lines.append(f"First name: {user.first_name}")
        await message.reply_text("\n".join(lines))

    async def pro_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if not self._settings.buildwise_pro_enabled or user.id not in self._allowed_ids:
            await message.reply_text(
                "BuildWise Pro is currently a limited 10-person beta. "
                "This account does not currently have Pro beta access."
            )
            return
        used = await self._limiter.current(str(user.id), date.today())
        await message.reply_text(
            "🧰 BuildWise Pro Beta access: Active\n"
            f"Reports used today: {used} / {self._settings.buildwise_pro_daily_request_limit}\n"
            "Supported outputs: PDF + DOCX\n\n"
            "Privacy: BuildWise Pro beta analytics do not store drawings, site notes, "
            "report bodies, uploaded templates, or project documents.\n\n"
            "Use /pro to start a report."
        )

    async def begin_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        state = self._state(context)
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return await super().begin_report(update, context)
        try:
            state["analytics_session_id"] = await self._analytics.start_session(user.id)
        except ProBetaAnalyticsError:
            state["analytics_session_id"] = None
            logger.warning("BuildWise Pro beta analytics session start failed user_id=%s", user.id)
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

        session_id = state.get("analytics_session_id")
        if not isinstance(session_id, str):
            try:
                session_id = await self._analytics.start_session(user.id)
            except ProBetaAnalyticsError:
                session_id = None
                logger.warning("BuildWise Pro beta analytics recovery start failed user_id=%s", user.id)

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
                if session_id:
                    await self._record_generation_safely(
                        session_id=session_id,
                        telegram_id=user.id,
                        success=False,
                        latency_ms=latency_ms,
                        pdf_success=None,
                        docx_success=None,
                    )
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
            rendered: list[tuple[bytes, str, str]] = []
            pdf_rendered = False
            docx_rendered = False
            try:
                rendered.append(
                    (
                        render_report_pdf(report, project),
                        f"BuildWise_Pro_Draft_Report_{safe_name}.pdf",
                        "PDF",
                    )
                )
                pdf_rendered = True
            except Exception:
                logger.exception("BuildWise Pro PDF rendering failed user_id=%s", user.id)
            try:
                rendered.append(
                    (
                        render_report_docx(report, project),
                        f"BuildWise_Pro_Draft_Report_{safe_name}.docx",
                        "DOCX",
                    )
                )
                docx_rendered = True
            except Exception:
                logger.exception("BuildWise Pro DOCX rendering failed user_id=%s", user.id)
            if not pdf_rendered or not docx_rendered:
                try:
                    rendered.append(
                        (
                            render_report_html(report, project),
                            f"BuildWise_Pro_Draft_Report_{safe_name}.html",
                            "HTML fallback",
                        )
                    )
                except Exception:
                    logger.exception("BuildWise Pro HTML fallback failed user_id=%s", user.id)

            pdf_success = False
            docx_success = False
            sent_labels: list[str] = []
            if update.effective_chat:
                for data, filename, label in rendered:
                    try:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=InputFile(BytesIO(data), filename=filename),
                            caption=(
                                f"BuildWise Pro {label} draft — professional review required before real-world use."
                            ),
                        )
                        sent_labels.append(label)
                        if label == "PDF":
                            pdf_success = True
                        elif label == "DOCX":
                            docx_success = True
                    except Exception:
                        logger.exception(
                            "BuildWise Pro export delivery failed user_id=%s format=%s",
                            user.id,
                            label,
                        )

            latency_ms = round((monotonic() - started) * 1000)
            if session_id:
                await self._record_generation_safely(
                    session_id=session_id,
                    telegram_id=user.id,
                    success=True,
                    latency_ms=latency_ms,
                    pdf_success=pdf_success,
                    docx_success=docx_success,
                )

            summary = report.summary.strip()
            if len(summary) > 900:
                summary = summary[:897].rstrip() + "…"
            files = " + ".join(sent_labels) if sent_labels else "No export delivered"
            feedback_markup = _feedback_buttons(session_id) if session_id else None
            await query.edit_message_text(
                "✅ BuildWise Pro draft complete\n\n"
                "⚠️ DRAFT ENGINEERING ASSISTANCE ONLY\n\n"
                f"Summary: {summary}\n\n"
                f"Files: {files}\n"
                "Every output requires professional review before real-world use.\n\n"
                "Beta feedback should not include confidential engineering or project information.",
                reply_markup=feedback_markup,
            )
            logger.info(
                "BuildWise Pro beta generation metadata user_id=%s pdf_success=%s docx_success=%s usage=%s/%s latency_ms=%s",
                user.id,
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
        if query is None or user is None or not query.data:
            return
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        rating = "helpful" if parts[2] == "up" else "needs_improvement"
        session_id = parts[3]
        try:
            if not await self._analytics.session_belongs_to(session_id, user.id):
                await query.answer("That feedback link is not valid for this account.", show_alert=True)
                return
            await self._analytics.record_rating(
                session_id=session_id, telegram_id=user.id, rating=rating
            )
        except (ProBetaAnalyticsError, ValueError):
            logger.warning("BuildWise Pro beta rating save failed user_id=%s", user.id)
            await query.answer("Feedback could not be saved right now.", show_alert=True)
            return
        await query.answer("Thanks — feedback saved.", show_alert=False)

    async def feedback_comment_prompt(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None or not query.data:
            return ConversationHandler.END
        session_id = query.data.rsplit(":", 1)[-1]
        try:
            valid = await self._analytics.session_belongs_to(session_id, user.id)
        except (ProBetaAnalyticsError, ValueError):
            valid = False
        if not valid:
            await query.answer("That feedback link is not valid for this account.", show_alert=True)
            return ConversationHandler.END
        await query.answer()
        context.user_data[FEEDBACK_SESSION_KEY] = session_id
        await query.message.reply_text(
            "💬 Send a short BuildWise Pro beta comment (maximum 1,000 characters).\n\n"
            "Do not include confidential engineering/project information, drawings, site notes, "
            "client data, secrets, or report content. Use /cancel to stop."
        )
        return FEEDBACK_TEXT

    async def feedback_comment(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        message = update.effective_message
        user = update.effective_user
        session_id = context.user_data.get(FEEDBACK_SESSION_KEY)
        text = (message.text if message else "") or ""
        if message is None or user is None or not isinstance(session_id, str):
            return ConversationHandler.END
        cleaned = text.strip()
        if not 1 <= len(cleaned) <= 1000:
            await message.reply_text("Keep beta feedback between 1 and 1,000 characters.")
            return FEEDBACK_TEXT
        try:
            await self._analytics.record_comment(
                session_id=session_id,
                telegram_id=user.id,
                feedback_text=cleaned,
            )
        except (ProBetaAnalyticsError, ValueError):
            logger.warning("BuildWise Pro beta text feedback save failed user_id=%s", user.id)
            await message.reply_text("I couldn't save that feedback right now. Please try again later.")
            return ConversationHandler.END
        finally:
            context.user_data.pop(FEEDBACK_SESSION_KEY, None)
        await message.reply_text("✅ Thanks. Your BuildWise Pro beta feedback was saved.")
        return ConversationHandler.END

    async def feedback_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.pop(FEEDBACK_SESSION_KEY, None)
        if update.effective_message:
            await update.effective_message.reply_text("Beta feedback cancelled.")
        return ConversationHandler.END

    async def pro_beta_stats(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        admin_configured = admin_configuration_error(self._settings) is None
        if not admin_configured or user.id not in self._settings.admin_telegram_ids:
            await message.reply_text("This command is restricted to an authorized BuildWise admin.")
            return
        try:
            stats = await self._analytics.stats(allowlisted_count=len(self._allowed_ids))
        except ProBetaAnalyticsError:
            logger.warning("BuildWise Pro beta stats unavailable for admin user_id=%s", user.id)
            await message.reply_text("BuildWise Pro beta statistics are unavailable right now.")
            return

        def pct(value) -> str:
            return "n/a" if value is None else f"{value}%"

        latency = stats["average_latency_ms"]
        latency_text = "n/a" if latency is None else f"{latency} ms"
        await message.reply_text(
            "📊 BuildWise Pro Beta 1.1\n\n"
            f"Allowlisted testers: {stats['allowlisted_testers']} / 10\n"
            f"Testers who used Pro: {stats['testers_used']}\n"
            f"Reports started: {stats['reports_started']}\n"
            f"Reports successfully completed: {stats['reports_completed']}\n"
            f"Generation failures: {stats['generation_failures']}\n"
            f"Average generation latency: {latency_text}\n"
            f"PDF success rate: {pct(stats['pdf_success_rate'])}\n"
            f"DOCX success rate: {pct(stats['docx_success_rate'])}\n"
            f"👍 Helpful: {stats['helpful_count']}\n"
            f"👎 Needs improvement: {stats['needs_improvement_count']}\n"
            f"Feedback rate: {pct(stats['feedback_rate'])}\n\n"
            "Token usage: unavailable in the current Pro OpenAI adapter.\n"
            "AI cost estimate: unavailable; no cost is inferred without reliable usage/pricing metadata."
        )

    async def _record_generation_safely(self, **kwargs) -> None:
        try:
            await self._analytics.record_generation(**kwargs)
        except ProBetaAnalyticsError:
            logger.warning(
                "BuildWise Pro beta analytics generation write failed user_id=%s",
                kwargs.get("telegram_id"),
            )
