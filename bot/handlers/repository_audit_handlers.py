"""Repository-audit hardening for the production Telegram student experience.

This layer keeps the existing BuildWise UX while fixing attachment retention,
post-download size enforcement, and in-memory sketch report lifetime.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from telegram import Update
from telegram.ext import Application, ContextTypes

from bot.admin import admin_configuration_error
from bot.domain import Mode, RequestPayload, UserProfile
from bot.handlers.elevation_bot_handlers import ElevationBotHandlers
from bot.handlers.navigation_bot_handlers import _current_study_request_kind
from bot.services.supabase_service import SupabaseError
from bot.study_experience import detect_study_request_kind

logger = logging.getLogger(__name__)

SKETCH_REPORT_TTL_SECONDS = 30 * 60
SKETCH_REPORT_CLEANUP_SECONDS = 5 * 60
MAX_SKETCH_REPORTS_PER_USER = 2


def visible_admin_navigation_ids(settings, admin_ids: frozenset[int]) -> frozenset[int]:
    """Expose Pro beta stats only while Pro and admin credentials are both active."""
    if not settings.buildwise_pro_enabled or admin_configuration_error(settings) is not None:
        return frozenset()
    return admin_ids


class RepositoryAuditHandlers(ElevationBotHandlers):
    """Production handlers with repository-audit fixes layered on existing behavior."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._admin_navigation_ids = visible_admin_navigation_ids(
            self._settings, self._admin_navigation_ids
        )

    async def post_init(self, application: Application) -> None:
        await super().post_init(application)
        if application.job_queue is not None:
            application.job_queue.run_repeating(
                self._cleanup_sketch_report_cache,
                interval=SKETCH_REPORT_CLEANUP_SECONDS,
                first=SKETCH_REPORT_CLEANUP_SECONDS,
                name="buildwise-sketch-cache-cleanup",
            )

    @staticmethod
    def _prune_sketch_reports(user_data: dict[str, Any], *, now: float | None = None) -> None:
        reports = user_data.get("sketch_reports")
        if not isinstance(reports, dict):
            return
        current = monotonic() if now is None else now

        for key, record in list(reports.items()):
            if not isinstance(record, dict):
                reports.pop(key, None)
                continue
            created = record.get("created_at_monotonic")
            if not isinstance(created, (int, float)):
                record["created_at_monotonic"] = current
                created = current
            if current - float(created) >= SKETCH_REPORT_TTL_SECONDS:
                reports.pop(key, None)

        while len(reports) > MAX_SKETCH_REPORTS_PER_USER:
            oldest = min(
                reports,
                key=lambda key: float(
                    reports[key].get("created_at_monotonic", current)
                    if isinstance(reports[key], dict)
                    else current
                ),
            )
            reports.pop(oldest, None)

        if not reports:
            user_data.pop("sketch_reports", None)

    async def _cleanup_sketch_report_cache(
        self, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        now = monotonic()
        for user_data in context.application.user_data.values():
            if isinstance(user_data, dict):
                self._prune_sketch_reports(user_data, now=now)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        raw_for_kind = (message.text or message.caption or "") if message is not None else ""
        token = _current_study_request_kind.set(detect_study_request_kind(raw_for_kind))
        try:
            profile = await self._profile(update)
            if message is None:
                return
            raw_text = (message.text or message.caption or "").strip()

            if context.user_data.pop("awaiting_timezone", False):
                await self._set_timezone(update, profile, raw_text)
                return
            if context.user_data.pop("awaiting_custom_goal", False):
                await self._set_goal(update, profile, raw_text)
                return
            if context.user_data.pop("awaiting_reminder_time", False):
                await self._set_custom_reminder(update, profile, raw_text)
                return
            if context.user_data.pop("awaiting_practice_topic", False):
                await self._start_practice(update, context, raw_text)
                return
            if context.user_data.pop("awaiting_check_answer", False):
                await self._check_student_answer(update, context, profile, raw_text)
                return

            caption_command = self._caption_command(raw_text)
            caption_mode, text = self._extract_caption_mode(raw_text)
            if caption_mode is not None:
                context.user_data["mode"] = caption_mode.value
            payload = RequestPayload(text=text)

            if message.photo:
                photo = message.photo[-1]
                if not await self._check_size(update, photo.file_size):
                    return
                image_data = await self._download_file(context, photo.file_id)
                if not await self._check_size(update, len(image_data)):
                    return
                payload.image_bytes = image_data
                payload.image_mime_type = "image/jpeg"
                payload.filename = "telegram-photo.jpg"
            elif message.document:
                document = message.document
                if not await self._check_size(update, document.file_size):
                    return
                mime_type = (document.mime_type or "").lower()
                filename = document.file_name or "document"
                data = await self._download_file(context, document.file_id)
                if not await self._check_size(update, len(data)):
                    return
                if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
                    payload.pdf_bytes = data
                    payload.filename = filename
                elif mime_type.startswith("image/"):
                    payload.image_bytes = data
                    payload.image_mime_type = mime_type
                    payload.filename = filename
                else:
                    await message.reply_text(
                        "I can read text, Telegram photos, image files, and PDFs. "
                        "Please convert this file to PDF or an image."
                    )
                    return

            if not payload.text and not payload.has_attachment:
                await message.reply_text("Send a question, photo, sketch, floor plan, or PDF.")
                return

            selected = caption_mode or self._selected_mode(context)
            ignore_history = bool(context.user_data.pop("ignore_history_once", False))
            decision = self._router.route(
                payload.text,
                selected_mode=selected,
                has_image=payload.has_image,
                has_pdf=payload.has_pdf,
                filename=payload.filename,
            )
            if decision.needs_clarification:
                # Keep the pending attachment only in the short-lived Telegram process
                # state. Persist it to private storage only after an AI request succeeds.
                context.user_data["pending_request"] = {
                    "text": payload.text,
                    "filename": payload.filename,
                    "image_mime_type": payload.image_mime_type,
                    "image_bytes": payload.image_bytes,
                    "pdf_bytes": payload.pdf_bytes,
                    "is_pdf": payload.has_pdf,
                    "ignore_history": ignore_history,
                }
                await message.reply_text(
                    "What would you like me to do with this?",
                    reply_markup=self._clarification_markup(),
                )
                return

            activity = "revision" if caption_command == "study" else "solve"
            await self._respond(
                update,
                context,
                profile,
                decision.mode,
                payload,
                activity_type=activity if decision.mode is Mode.STUDY else None,
                ignore_history=ignore_history,
            )
        finally:
            _current_study_request_kind.reset(token)

    @staticmethod
    def _clarification_markup():
        # Import lazily to avoid changing the existing keyboard module contract.
        from bot.keyboards import clarification_keyboard

        return clarification_keyboard()

    async def _handle_route_choice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        choice: str,
    ) -> None:
        pending = context.user_data.pop("pending_request", None)
        if not isinstance(pending, dict):
            await update.effective_message.reply_text(
                "That upload is no longer pending. Please send it again."
            )
            return

        mode = Mode.STUDY if choice in {"study", "explain"} else Mode(choice)
        payload = RequestPayload(
            text=str(pending.get("text") or ""),
            filename=pending.get("filename"),
        )
        if pending.get("is_pdf"):
            file_data = pending.get("pdf_bytes")
            if not isinstance(file_data, bytes) or not file_data:
                await update.effective_message.reply_text(
                    "That upload is no longer pending. Please send it again."
                )
                return
            payload.pdf_bytes = file_data
        else:
            file_data = pending.get("image_bytes")
            if not isinstance(file_data, bytes) or not file_data:
                await update.effective_message.reply_text(
                    "That upload is no longer pending. Please send it again."
                )
                return
            payload.image_bytes = file_data
            payload.image_mime_type = str(
                pending.get("image_mime_type") or "image/jpeg"
            )

        await self._respond(
            update,
            context,
            profile,
            mode,
            payload,
            activity_type="solve" if mode is Mode.STUDY else None,
            ignore_history=bool(pending.get("ignore_history", False)),
        )

    async def _persist_successful_attachment(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        payload: RequestPayload,
    ) -> None:
        if not profile.save_history or not payload.has_attachment:
            return
        conversation_id = context.user_data.get("last_conversation_id")
        if not conversation_id:
            return

        data = payload.image_bytes if payload.has_image else payload.pdf_bytes
        if not isinstance(data, bytes) or not data:
            return
        mime_type = payload.image_mime_type if payload.has_image else "application/pdf"
        reference: str | None = None
        try:
            reference = await self._database.upload_file(
                user_id=profile.id,
                filename=payload.filename or "upload.bin",
                mime_type=mime_type or "application/octet-stream",
                data=data,
            )
            attach = getattr(self._database, "attach_file_to_conversation", None)
            if attach is None:
                raise SupabaseError("Attachment persistence helper is unavailable")
            await attach(
                user_id=profile.id,
                conversation_id=str(conversation_id),
                file_reference=reference,
            )
        except SupabaseError:
            logger.exception("Could not persist successful student attachment")
            if reference:
                try:
                    await self._database.delete_file(reference)
                except SupabaseError:
                    logger.warning("Could not clean up unattached student upload")
            return

        payload.file_reference = reference
        context.user_data["last_file_reference"] = reference

    async def _respond(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        mode: Mode,
        payload: RequestPayload,
        *,
        activity_type: str | None = None,
        topic: str | None = None,
        extra_history=None,
        ignore_history: bool = False,
    ) -> str | None:
        self._prune_sketch_reports(context.user_data)
        answer = await super()._respond(
            update,
            context,
            profile,
            mode,
            payload,
            activity_type=activity_type,
            topic=topic,
            extra_history=extra_history,
            ignore_history=ignore_history,
        )
        if answer is None:
            return None

        if mode is Mode.SKETCH:
            reports = context.user_data.get("sketch_reports")
            if isinstance(reports, dict):
                now = monotonic()
                for record in reports.values():
                    if isinstance(record, dict) and "created_at_monotonic" not in record:
                        record["created_at_monotonic"] = now
                self._prune_sketch_reports(context.user_data, now=now)

        await self._persist_successful_attachment(context, profile, payload)
        return answer

    async def _generate_view(self, *args, **kwargs) -> None:
        context = args[1] if len(args) > 1 else kwargs.get("context")
        if context is not None:
            self._prune_sketch_reports(context.user_data)
        await super()._generate_view(*args, **kwargs)

    @staticmethod
    def _clear_learning_context(context: ContextTypes.DEFAULT_TYPE) -> None:
        ElevationBotHandlers._clear_learning_context(context)
        context.user_data.pop("sketch_reports", None)
