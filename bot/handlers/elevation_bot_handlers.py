"""Sketch elevation extension layered on the existing production Telegram handlers."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from time import monotonic
from uuid import uuid4

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot.domain import ConversationTurn, Mode, RequestPayload, UserProfile
from bot.formatting import format_response, sketch_preview
from bot.handlers.admin_notification_bot_handlers import AdminNotificationBotHandlers
from bot.keyboards import elevation_result_keyboard, sketch_analysis_keyboard
from bot.services.openai_service import AIServiceError
from bot.services.sketch_elevation_service import (
    SketchElevationError,
    SketchElevationService,
)
from bot.services.supabase_service import SupabaseError
from bot.sketch_elevation import elevation_caption

logger = logging.getLogger(__name__)

_SUPPORTED_ELEVATION_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


class ElevationBotHandlers(AdminNotificationBotHandlers):
    """Add real sketch-to-front-elevation generation without changing Pro/Admin flows."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._elevation = SketchElevationService(self._settings)

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        data = (query.data or "") if query else ""
        if data.startswith("elevgen:") or data.startswith("elevregen:"):
            if query is None or query.message is None:
                return
            if update.effective_chat is None or update.effective_chat.type != "private":
                await query.answer("Open your private BuildWise chat.", show_alert=True)
                return
            await query.answer()
            profile = await self._profile(update)
            key = data.partition(":")[2]
            await self._generate_elevation(
                update,
                context,
                profile,
                key,
                regenerate=data.startswith("elevregen:"),
            )
            return
        await super().handle_callback(update, context)

    async def _generate_elevation(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        report_key: str,
        *,
        regenerate: bool,
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or query.message is None or user is None:
            return
        reports = context.user_data.get("sketch_reports", {})
        record = reports.get(report_key)
        if not isinstance(record, dict):
            await query.message.reply_text(
                "This sketch session has expired. Please upload the sketch again."
            )
            return
        image_bytes = record.get("image")
        mime_type = str(record.get("image_mime_type") or "image/jpeg").lower()
        if not isinstance(image_bytes, bytes) or not image_bytes:
            await query.message.reply_text(
                "I need the original sketch image to generate the elevation. Please upload the sketch again as a JPG or PNG."
            )
            return
        if mime_type not in _SUPPORTED_ELEVATION_MIME_TYPES:
            await query.message.reply_text(
                "Elevation generation currently supports JPG, PNG, and WebP sketch images."
            )
            return

        lock = self._user_locks.setdefault(user.id, asyncio.Lock())
        if lock.locked():
            await query.message.reply_text(
                "BuildWise is already working on your previous request."
            )
            return

        async with lock:
            usage = await self._database.consume_usage(
                profile, self._settings.daily_ai_request_limit
            )
            if not usage.allowed:
                await query.message.reply_text(self._reset_message(profile, usage.limit))
                return

            loading = await query.message.reply_text("🏠 Generating elevation...")
            variation = int(record.get("elevation_variation", 0)) + (1 if regenerate else 0)
            try:
                elevation_bytes, spec = await self._elevation.generate(
                    image_bytes=image_bytes,
                    image_mime_type=mime_type,
                    analysis=str(record.get("answer") or ""),
                    variation=variation,
                )
            except SketchElevationError as exc:
                await self._database.refund_usage(profile)
                await loading.edit_text(exc.public_message)
                return
            except Exception:
                await self._database.refund_usage(profile)
                logger.exception("Unexpected sketch elevation generation failure")
                await loading.edit_text(
                    "I couldn't generate the elevation this time. Your request was refunded; please try again."
                )
                return

            record["elevation"] = elevation_bytes
            record["elevation_assumptions"] = list(spec.assumptions)
            record["elevation_variation"] = variation
            record["elevation_spec"] = spec.prompt_json()

            output = BytesIO(elevation_bytes)
            output.name = "BuildWise-concept-elevation.png"
            try:
                await query.message.reply_photo(
                    photo=output,
                    caption=elevation_caption(spec),
                    reply_markup=elevation_result_keyboard(report_key),
                )
            except TelegramError:
                await self._database.refund_usage(profile)
                logger.exception("Telegram elevation delivery failed")
                await loading.edit_text(
                    "The elevation was generated but Telegram could not deliver it. Your request was refunded; please try again."
                )
                return
            try:
                await loading.delete()
            except TelegramError:
                await loading.edit_text("✅ Elevation ready.")

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
        extra_history: list[ConversationTurn] | None = None,
        ignore_history: bool = False,
    ) -> str | None:
        if mode is not Mode.SKETCH:
            return await super()._respond(
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

        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return None
        lock = self._user_locks.setdefault(user.id, asyncio.Lock())
        if lock.locked():
            await message.reply_text("BuildWise is already working on your previous request.")
            return None

        async with lock:
            now = monotonic()
            elapsed = now - self._request_times.get(user.id, 0.0)
            if elapsed < self._settings.request_cooldown_seconds:
                wait = max(1, round(self._settings.request_cooldown_seconds - elapsed))
                await message.reply_text(
                    f"Please wait {wait} second before sending another AI request."
                )
                return None

            usage = await self._database.consume_usage(
                profile, self._settings.daily_ai_request_limit
            )
            if not usage.allowed:
                await message.reply_text(self._reset_message(profile, usage.limit))
                return None
            self._request_times[user.id] = now

            loading = await message.reply_text(self._loading_text(mode))
            history: list[ConversationTurn] = []
            if profile.save_history and not ignore_history:
                rows = await self._database.recent_conversations(
                    profile.id,
                    mode=mode.value,
                    limit=max(1, self._settings.history_turns // 2),
                )
                for row in reversed(rows):
                    history.append(ConversationTurn("user", str(row["message"])))
                    history.append(ConversationTurn("assistant", str(row["response"])))
            history.extend(extra_history or [])

            try:
                answer = await self._ai.answer(
                    mode,
                    payload,
                    history=history,
                    answer_style=profile.answer_style,
                )
            except AIServiceError as exc:
                await self._database.refund_usage(profile)
                if payload.file_reference:
                    try:
                        await self._database.delete_file(payload.file_reference)
                    except SupabaseError:
                        logger.warning("Could not remove failed-request sketch upload")
                await loading.edit_text(exc.public_message)
                return None

            prompt_text = payload.text.strip() or self._attachment_label(payload)
            conversation_id = None
            if profile.save_history:
                try:
                    conversation_id = await self._database.save_conversation(
                        user_id=profile.id,
                        mode=mode.value,
                        message=prompt_text,
                        response=answer,
                        category=topic,
                        file_reference=payload.file_reference,
                    )
                except SupabaseError:
                    logger.exception("Sketch answer succeeded but history save failed")
                    if payload.file_reference:
                        try:
                            await self._database.delete_file(payload.file_reference)
                            payload.file_reference = None
                        except SupabaseError:
                            logger.warning("Could not clean up an unsaved sketch upload")
            elif payload.file_reference:
                try:
                    await self._database.delete_file(payload.file_reference)
                    payload.file_reference = None
                except SupabaseError:
                    logger.warning("Could not clean up a history-disabled sketch upload")

            context.user_data.update(
                {
                    "last_mode": mode.value,
                    "last_question": prompt_text,
                    "last_answer": answer,
                    "last_file_reference": payload.file_reference,
                    "last_conversation_id": conversation_id,
                }
            )

            report_key = str(uuid4())
            reports = context.user_data.setdefault("sketch_reports", {})
            reports[report_key] = {
                "answer": answer,
                "image": payload.image_bytes,
                "image_mime_type": payload.image_mime_type,
            }
            while len(reports) > 2:
                reports.pop(next(iter(reports)))

            mime_type = (payload.image_mime_type or "").lower()
            can_generate_elevation = bool(
                payload.image_bytes and mime_type in _SUPPORTED_ELEVATION_MIME_TYPES
            )
            markup = sketch_analysis_keyboard(
                report_key,
                can_generate_elevation=can_generate_elevation,
            )
            preview = format_response(mode, sketch_preview(answer))
            try:
                await loading.edit_text(preview, reply_markup=markup)
            except BadRequest:
                await message.reply_text(preview, reply_markup=markup)
            return answer
