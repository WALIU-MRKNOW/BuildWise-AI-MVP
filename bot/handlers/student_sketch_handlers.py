"""Pilot-only Telegram /draw command for original BuildWise student sketches."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.config import Settings
from bot.services.student_sketch_service import StudentSketchError, StudentSketchService
from bot.services.supabase_service import SupabaseService

logger = logging.getLogger(__name__)


def pilot_access_enabled(settings: Settings, telegram_id: int | None) -> bool:
    return bool(
        settings.student_sketch_enabled
        and telegram_id is not None
        and telegram_id in settings.student_sketch_allowed_ids
    )


class StudentSketchHandlers:
    def __init__(self, settings: Settings, database: SupabaseService) -> None:
        self._settings = settings
        self._database = database
        self._sketches = StudentSketchService(settings)
        self._locks: dict[int, asyncio.Lock] = {}

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("draw", self.draw))

    async def draw(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if message is None or user is None:
            return
        if not pilot_access_enabled(self._settings, user.id):
            await message.reply_text("Sketch Studio is not available for your account yet.")
            return

        description = " ".join(context.args or []).strip()
        if not description or len(description) > 800:
            await message.reply_text(
                "Describe your educational drawing in 1–800 characters. "
                "Example: /draw A labelled cross-section of a raft foundation"
            )
            return

        lock = self._locks.setdefault(user.id, asyncio.Lock())
        if lock.locked():
            await message.reply_text("Your previous sketch is still being drawn. Please wait.")
            return

        async with lock:
            # Pilot cap is intentionally small and separate from ordinary AI requests.
            # This per-process guard resets on restart: pilot access MUST stay allowlisted.
            utc_day = datetime.now(timezone.utc).date().isoformat()
            quota = context.user_data.get("student_sketch_quota")
            if not isinstance(quota, dict) or quota.get("utc_day") != utc_day:
                quota = {"utc_day": utc_day, "count": 0}
            if quota["count"] >= self._settings.student_sketch_daily_limit:
                await message.reply_text(
                    "You have used today's Sketch Studio pilot allowance. "
                    "It resets at 00:00 UTC."
                )
                return

            profile = await self._database.upsert_user(
                user.id, user.username, user.full_name or "Student"
            )
            usage = await self._database.consume_usage(
                profile, self._settings.daily_ai_request_limit
            )
            if not usage.allowed:
                await message.reply_text(
                    f"You have used today's {usage.limit} BuildWise AI requests."
                )
                return

            # Reserve the sketch slot before the paid API call. Failure refunds both.
            quota["count"] += 1
            context.user_data["student_sketch_quota"] = quota
            try:
                notice = await message.reply_text("✏️ Drawing your educational sketch…")
                drawing = await self._sketches.draw(description)
                image = BytesIO(drawing)
                image.name = "buildwise-educational-sketch.png"
                await message.reply_document(
                    document=image,
                    filename=image.name,
                    caption=(
                        "✏️ BuildWise Sketch Studio · Educational concept only. "
                        "Not to scale unless verified; check dimensions and technical details "
                        "with your course instructor or a qualified professional."
                    ),
                )
                try:
                    await notice.delete()
                except TelegramError:
                    pass
            except StudentSketchError as exc:
                quota["count"] -= 1
                await self._database.refund_usage(profile)
                await message.reply_text(str(exc))
            except TelegramError:
                # The API request may have been billed even if Telegram delivery failed.
                logger.exception("Sketch generated but Telegram delivery failed")
                await message.reply_text(
                    "The sketch could not be delivered through Telegram. "
                    "Please try again later."
                )
