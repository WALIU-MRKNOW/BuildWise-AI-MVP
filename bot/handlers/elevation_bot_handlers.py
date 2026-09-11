"""Sketch multi-view extension layered on the existing production Telegram handlers."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
import logging
from time import monotonic
from uuid import uuid4

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot.domain import ConversationTurn, Mode, RequestPayload, UserProfile
from bot.formatting import format_response, sketch_preview
from bot.handlers.admin_notification_bot_handlers import AdminNotificationBotHandlers
from bot.keyboards import (
    elevation_result_keyboard,
    more_views_keyboard,
    sketch_analysis_keyboard,
    view_result_keyboard,
)
from bot.services.openai_service import AIServiceError
from bot.services.sketch_elevation_service import (
    SketchElevationError,
    SketchElevationService,
)
from bot.services.supabase_service import SupabaseError
from bot.sketch_elevation import (
    SKETCH_VIEW_LABELS,
    SketchElevationSpec,
    normalize_sketch_view,
    view_caption,
)

logger = logging.getLogger(__name__)

_SUPPORTED_ELEVATION_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

_VIEW_LOADING = {
    "front": "🏠 Generating front elevation...",
    "left": "⬅️ Generating left side elevation...",
    "right": "➡️ Generating right side elevation...",
    "rear": "🔙 Generating rear elevation...",
    "top": "🔝 Generating top / roof view...",
}

_VIEW_FILENAMES = {
    "front": "BuildWise-front-elevation.png",
    "left": "BuildWise-left-side-elevation.png",
    "right": "BuildWise-right-side-elevation.png",
    "rear": "BuildWise-rear-elevation.png",
    "top": "BuildWise-top-roof-view.png",
}


class ElevationBotHandlers(AdminNotificationBotHandlers):
    """Add consistent sketch multi-view generation without changing Pro/Admin flows."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._elevation = SketchElevationService(self._settings)

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        data = (query.data or "") if query else ""
        if data.startswith(("elevgen:", "elevregen:", "elevmenu:", "elevview:", "elevback:")):
            if query is None or query.message is None:
                return
            if update.effective_chat is None or update.effective_chat.type != "private":
                await query.answer("Open your private BuildWise chat.", show_alert=True)
                return
            await query.answer()

            if data.startswith("elevmenu:"):
                await self._show_more_views(
                    query,
                    context,
                    data.partition(":")[2],
                )
                return
            if data.startswith("elevback:"):
                await self._show_front_actions(
                    query,
                    context,
                    data.partition(":")[2],
                )
                return

            view = "front"
            regenerate = False
            if data.startswith("elevgen:"):
                key = data.partition(":")[2]
            elif data.startswith("elevview:"):
                tail = data[len("elevview:") :]
                view, separator, key = tail.partition(":")
                if not separator:
                    await query.message.reply_text("That building view is no longer available.")
                    return
            else:
                regenerate = True
                tail = data[len("elevregen:") :]
                if ":" in tail:
                    view, key = tail.split(":", 1)
                else:
                    # Backward compatibility for front-elevation buttons sent
                    # before the multi-view release.
                    key = tail
                    view = "front"

            try:
                view = normalize_sketch_view(view)
            except ValueError:
                await query.message.reply_text("That building view is not supported.")
                return
            profile = await self._profile(update)
            await self._generate_view(
                update,
                context,
                profile,
                key,
                view=view,
                regenerate=regenerate,
            )
            return
        await super().handle_callback(update, context)

    @staticmethod
    def _record(context: ContextTypes.DEFAULT_TYPE, report_key: str) -> dict | None:
        reports = context.user_data.get("sketch_reports", {})
        if not isinstance(reports, dict):
            return None
        record = reports.get(report_key)
        return record if isinstance(record, dict) else None

    @staticmethod
    def _stored_spec(record: dict) -> SketchElevationSpec | None:
        raw = record.get("elevation_spec")
        if isinstance(raw, dict):
            return SketchElevationSpec.from_mapping(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return SketchElevationSpec.from_mapping(parsed)
        return None

    async def _show_more_views(self, query, context, report_key: str) -> None:
        record = self._record(context, report_key)
        if record is None:
            await query.message.reply_text(
                "This sketch session has expired. Please upload the sketch again."
            )
            return
        await query.message.reply_text(
            "🧱 More Views\n\nChoose another view of the same interpreted building:",
            reply_markup=more_views_keyboard(report_key),
        )

    async def _show_front_actions(self, query, context, report_key: str) -> None:
        record = self._record(context, report_key)
        if record is None:
            await query.message.reply_text(
                "This sketch session has expired. Please upload the sketch again."
            )
            return
        try:
            await query.message.edit_text(
                "🏠 Front elevation actions",
                reply_markup=elevation_result_keyboard(report_key),
            )
        except BadRequest:
            await query.message.reply_text(
                "🏠 Front elevation actions",
                reply_markup=elevation_result_keyboard(report_key),
            )

    async def _generate_view(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        report_key: str,
        *,
        view: str,
        regenerate: bool,
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or query.message is None or user is None:
            return
        record = self._record(context, report_key)
        if record is None:
            await query.message.reply_text(
                "This sketch session has expired. Please upload the sketch again."
            )
            return
        image_bytes = record.get("image")
        mime_type = str(record.get("image_mime_type") or "image/jpeg").lower()
        if not isinstance(image_bytes, bytes) or not image_bytes:
            await query.message.reply_text(
                "I need the original sketch image to generate this view. Please upload the sketch again as a JPG or PNG."
            )
            return
        if mime_type not in _SUPPORTED_ELEVATION_MIME_TYPES:
            await query.message.reply_text(
                "Building-view generation currently supports JPG, PNG, and WebP sketch images."
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

            loading = await query.message.reply_text(_VIEW_LOADING[view])
            variations = record.get("view_variations")
            if not isinstance(variations, dict):
                variations = {}
                record["view_variations"] = variations
            legacy_front_variation = int(record.get("elevation_variation", 0) or 0)
            current_variation = int(
                variations.get(view, legacy_front_variation if view == "front" else 0) or 0
            )
            variation = current_variation + (1 if regenerate else 0)
            reusable_spec = self._stored_spec(record)

            try:
                view_bytes, spec = await self._elevation.generate(
                    image_bytes=image_bytes,
                    image_mime_type=mime_type,
                    analysis=str(record.get("answer") or ""),
                    view=view,
                    spec=reusable_spec,
                    variation=variation,
                )
            except SketchElevationError as exc:
                await self._database.refund_usage(profile)
                await loading.edit_text(exc.public_message)
                return
            except Exception:
                await self._database.refund_usage(profile)
                logger.exception("Unexpected sketch %s-view generation failure", view)
                await loading.edit_text(
                    "I couldn't generate that building view this time. Your request was refunded; please try again."
                )
                return

            views = record.get("views")
            if not isinstance(views, dict):
                views = {}
                record["views"] = views
            views[view] = view_bytes
            variations[view] = variation
            record["elevation_assumptions"] = list(spec.assumptions)
            record["elevation_spec"] = spec.prompt_json()
            if view == "front":
                # Preserve the old fields for existing report/download compatibility.
                record["elevation"] = view_bytes
                record["elevation_variation"] = variation

            output = BytesIO(view_bytes)
            output.name = _VIEW_FILENAMES[view]
            markup = (
                elevation_result_keyboard(report_key)
                if view == "front"
                else view_result_keyboard(report_key, view)
            )
            try:
                await query.message.reply_photo(
                    photo=output,
                    caption=view_caption(spec, view),
                    reply_markup=markup,
                )
            except TelegramError:
                await self._database.refund_usage(profile)
                logger.exception("Telegram %s-view delivery failed", view)
                await loading.edit_text(
                    "The view was generated but Telegram could not deliver it. Your request was refunded; please try again."
                )
                return
            try:
                await loading.delete()
            except TelegramError:
                await loading.edit_text(f"✅ {SKETCH_VIEW_LABELS[view]} ready.")

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
                "views": {},
                "view_variations": {},
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
