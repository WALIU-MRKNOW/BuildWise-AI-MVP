"""BuildWise Telegram experience: home, analysis, study, practice, and settings."""

from __future__ import annotations

import asyncio
from datetime import time
import logging
from time import monotonic
from typing import Any

from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
from uuid import uuid4
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot.config import Settings
from bot.domain import (
    ConversationTurn,
    Mode,
    QuizQuestion,
    RequestPayload,
    StudyCompletion,
    UserProfile,
)
from bot.formatting import format_response, split_for_telegram, sketch_preview
from bot.keyboards import (
    clarification_keyboard,
    follow_up_keyboard,
    goal_keyboard,
    history_confirm_keyboard,
    history_delete_keyboard,
    home_keyboard,
    mode_keyboard,
    practice_count_keyboard,
    practice_difficulty_keyboard,
    quiz_answer_keyboard,
    quiz_next_keyboard,
    reminder_keyboard,
    settings_keyboard,
)
from bot.router import RequestRouter
from bot.services.openai_service import AIServiceError, OpenAIService
from bot.services.reminder_service import ReminderService
from bot.services.supabase_service import (
    SupabaseError,
    SupabaseService,
    validate_timezone,
)

logger = logging.getLogger(__name__)

MODE_ALIASES = {
    "study": Mode.STUDY,
    "solve": Mode.STUDY,
    "quiz": Mode.STUDY,
    "practice": Mode.STUDY,
    "sketch": Mode.SKETCH,
    "architect": Mode.SKETCH,
    "architecture": Mode.SKETCH,
    "plan": Mode.SKETCH,
    "building": Mode.BUILDING,
    "defect": Mode.BUILDING,
}

HELP_TEXT = """🏠 BUILDWISE AI
Plan • Learn • Analyze • Build Better

Send text, a question photo, building photo, sketch, floor plan, or PDF directly.

/solve [question] — study and solve
/sketch — analyze a sketch or floor plan
/building — analyze a building photo
/practice — start Quick Practice
/history — recent work and saved items
/progress — weekly study summary
/goal [number] — daily question goal
/reminders — study reminder settings
/timezone [Area/City] — set your timezone
/settings — answer, history, and button preferences
/home — return to the home menu
/cancel — clear the selected mode
/help — show this guide

Less Stress. More Solutions. BuildWise. 🚀"""


class BotHandlers:
    def __init__(
        self,
        settings: Settings,
        ai_service: OpenAIService,
        router: RequestRouter,
        database: SupabaseService,
        reminders: ReminderService,
    ) -> None:
        self._settings = settings
        self._ai = ai_service
        self._router = router
        self._database = database
        self._reminders = reminders
        self._allowed_user_ids = settings.allowed_user_ids
        self._request_times: dict[int, float] = {}
        self._user_locks: dict[int, asyncio.Lock] = {}

    def register(self, application: Application) -> None:
        application.add_handler(TypeHandler(Update, self.guard_update), group=-1)
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("home", self.home_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("mode", self.mode_command))
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(CommandHandler("solve", self.solve))
        application.add_handler(CommandHandler("study", self.study))
        application.add_handler(CommandHandler("quiz", self.practice))
        application.add_handler(CommandHandler("practice", self.practice))
        application.add_handler(CommandHandler("building", self.building))
        application.add_handler(CommandHandler("sketch", self.sketch))
        application.add_handler(CommandHandler("architect", self.sketch))
        application.add_handler(CommandHandler("history", self.history_command))
        application.add_handler(CommandHandler("clear", self.clear_command))
        application.add_handler(CommandHandler("settings", self.settings_command))
        application.add_handler(CommandHandler("timezone", self.timezone_command))
        application.add_handler(CommandHandler("goal", self.goal_command))
        application.add_handler(CommandHandler("reminders", self.reminders_command))
        application.add_handler(CommandHandler("progress", self.progress_command))
        application.add_handler(CallbackQueryHandler(self.handle_callback))
        application.add_handler(
            MessageHandler(
                (filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL,
                self.handle_message,
            )
        )
        application.add_error_handler(self.handle_error)

    async def post_init(self, application: Application) -> None:
        await self._database.initialize()
        await application.bot.set_my_commands(
            [
                BotCommand("home", "Open the BuildWise home menu"),
                BotCommand("solve", "Solve and explain a student question"),
                BotCommand("practice", "Start Quick Practice"),
                BotCommand("sketch", "Analyze a sketch or floor plan"),
                BotCommand("building", "Analyze a building photo"),
                BotCommand("history", "View recent and saved work"),
                BotCommand("progress", "View your weekly progress"),
                BotCommand("goal", "Set your daily study goal"),
                BotCommand("reminders", "Choose a study reminder"),
                BotCommand("timezone", "Set your local timezone"),
                BotCommand("settings", "Change BuildWise settings"),
                BotCommand("help", "Show the complete guide"),
            ]
        )
        if application.job_queue is None:
            raise RuntimeError(
                "Telegram JobQueue is unavailable. Install python-telegram-bot[job-queue]."
            )
        application.job_queue.run_repeating(
            self._reminders.dispatch,
            interval=self._settings.reminder_poll_seconds,
            first=15,
            name="buildwise-reminder-dispatch",
        )

    async def shutdown(self, application: Application) -> None:
        await self._database.close()

    async def guard_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user = update.effective_user
        if self._allowed_user_ids and (
            user is None or user.id not in self._allowed_user_ids
        ):
            if update.effective_message:
                await update.effective_message.reply_text("This BuildWise bot is private.")
            raise ApplicationHandlerStop
        if not await self._database.claim_update(update.update_id):
            logger.info("Ignoring duplicate Telegram update id=%s", update.update_id)
            raise ApplicationHandlerStop

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        await self._show_home(update, profile)
        if not profile.timezone_confirmed and not profile.timezone_prompted:
            await update.effective_message.reply_text(
                "🌍 One quick setup step: send /timezone followed by your area, for "
                "example /timezone Africa/Lagos. This keeps streaks and reminders on your local day."
            )
            await self._database.mark_timezone_prompted(profile.id)

    async def home_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._show_home(update, await self._profile(update))

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._profile(update)
        await update.effective_message.reply_text(HELP_TEXT)

    async def _show_home(self, update: Update, profile: UserProfile) -> None:
        stats = await self._database.get_dashboard(profile)
        streak = f"🔥 Streak: {stats.current_streak} Day" + (
            "" if stats.current_streak == 1 else "s"
        )
        text = (
            "🏠 BUILDWISE AI\n"
            "Plan • Learn • Analyze • Build Better\n\n"
            "What can I help you with?\n\n"
            f"{streak}\n"
            f"🎯 Today: {stats.completed_today} / {stats.daily_target}\n\n"
            "You can also send a message, image, PDF, or sketch directly."
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=home_keyboard(timezone_confirmed=profile.timezone_confirmed),
        )

    async def mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._profile(update)
        if context.args:
            mode = MODE_ALIASES.get(context.args[0].lower())
            if mode is None:
                await update.effective_message.reply_text(
                    "Choose study, sketch, or building.", reply_markup=mode_keyboard()
                )
                return
            await self._activate_mode(update, context, mode)
            return
        selected = self._selected_mode(context)
        current = selected.label if selected else "Automatic detection"
        await update.effective_message.reply_text(
            f"Current mode: {current}", reply_markup=mode_keyboard()
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._profile(update)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Automatic detection is active. Send your next question or file."
        )

    async def solve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mode_command_with_optional_text(
            update, context, Mode.STUDY, activity_type="solve"
        )

    async def study(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mode_command_with_optional_text(
            update, context, Mode.STUDY, activity_type="revision"
        )

    async def building(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mode_command_with_optional_text(update, context, Mode.BUILDING)

    async def sketch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._mode_command_with_optional_text(update, context, Mode.SKETCH)

    async def _mode_command_with_optional_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        mode: Mode,
        *,
        activity_type: str | None = None,
    ) -> None:
        profile = await self._profile(update)
        context.user_data["mode"] = mode.value
        if context.args:
            await self._respond(
                update,
                context,
                profile,
                mode,
                RequestPayload(text=" ".join(context.args)),
                activity_type=activity_type,
            )
            return
        await self._activate_mode(update, context, mode)

    async def _activate_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, mode: Mode
    ) -> None:
        context.user_data["mode"] = mode.value
        prompts = {
            Mode.STUDY: "📚 Send a question, question photo, or PDF.",
            Mode.SKETCH: "📐 Send a sketch, floor plan, or architectural drawing.",
            Mode.BUILDING: "🏗️ Send a clear building or construction photo.",
        }
        await update.effective_message.reply_text(prompts[mode])

    async def practice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._profile(update)
        topic = " ".join(context.args).strip()
        await self._start_practice(update, context, topic or None)

    async def _start_practice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        topic: str | None = None,
    ) -> None:
        context.user_data.pop("practice_session", None)
        if not topic:
            context.user_data["awaiting_practice_topic"] = True
            await update.effective_message.reply_text(
                "🧠 Quick Practice\n\nWhat topic would you like to practice?"
            )
            return
        context.user_data["practice_setup"] = {"topic": topic}
        await update.effective_message.reply_text(
            f"Topic: {topic}\n\nChoose a difficulty:",
            reply_markup=practice_difficulty_keyboard(),
        )

    async def settings_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        await self._show_settings(update, profile)

    async def _show_settings(self, update: Update, profile: UserProfile) -> None:
        await update.effective_message.reply_text(
            self._settings_text(profile), reply_markup=settings_keyboard(profile)
        )

    async def timezone_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        if not context.args:
            context.user_data["awaiting_timezone"] = True
            await update.effective_message.reply_text(
                "Send your timezone in Area/City form, such as Africa/Lagos, "
                "Europe/London, or America/Chicago."
            )
            return
        await self._set_timezone(update, profile, " ".join(context.args))

    async def _set_timezone(
        self, update: Update, profile: UserProfile, value: str
    ) -> None:
        try:
            timezone_name = validate_timezone(value)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        await self._database.set_timezone(profile.id, timezone_name)
        await update.effective_message.reply_text(
            f"🌍 Timezone set to {timezone_name}. Streaks and reminders will use your local date."
        )

    async def goal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        if context.args:
            await self._set_goal(update, profile, context.args[0])
            return
        await update.effective_message.reply_text(
            f"🎯 Current daily goal: {profile.default_daily_goal}\n\nChoose a new goal:",
            reply_markup=goal_keyboard(),
        )

    async def _set_goal(
        self, update: Update, profile: UserProfile, raw_target: str
    ) -> None:
        try:
            target = int(raw_target)
            stats = await self._database.set_daily_goal(profile, target)
        except ValueError:
            await update.effective_message.reply_text(
                "Choose a whole number from 1 to 100. Example: /goal 5"
            )
            return
        await update.effective_message.reply_text(
            f"🎯 Daily goal set to {stats.daily_target}. Today: "
            f"{stats.completed_today} / {stats.daily_target}."
        )

    async def reminders_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        if not profile.timezone_confirmed:
            await update.effective_message.reply_text(
                "Set your timezone first with /timezone Area/City."
            )
            return
        await update.effective_message.reply_text(
            f"🔔 Study reminder ({profile.timezone})\n\nChoose one local time. "
            "BuildWise sends at most one automatic reminder per day.",
            reply_markup=reminder_keyboard(),
        )

    async def history_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        await self._show_history(update, profile)

    async def _show_history(self, update: Update, profile: UserProfile) -> None:
        conversations = await self._database.recent_conversations(profile.id, limit=6)
        saved = await self._database.recent_saved_items(profile.id, limit=3)
        if not conversations and not saved:
            await update.effective_message.reply_text(
                "📂 History is empty. Complete an analysis or save an answer first."
            )
            return
        sections = ["📂 BUILDWISE HISTORY"]
        for row in conversations:
            prompt = " ".join(str(row["message"]).split())
            response = " ".join(str(row["response"]).split())
            sections.append(
                f"{str(row['mode']).title()} · {str(row['created_at'])[:10]}\n"
                f"You: {self._trim(prompt, 130)}\n"
                f"BuildWise: {self._trim(response, 210)}"
            )
        if saved:
            sections.append("SAVED")
            for row in saved:
                sections.append(f"• {self._trim(str(row['title']), 100)}")
        await update.effective_message.reply_text(
            "\n\n".join(sections), reply_markup=history_delete_keyboard()
        )

    async def clear_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._profile(update)
        await update.effective_message.reply_text(
            "Delete all conversation history, saved items, and their stored files?",
            reply_markup=history_confirm_keyboard(),
        )

    async def progress_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        progress = await self._database.weekly_progress(profile)
        accuracy = f"{progress.accuracy:.1f}%" if progress.accuracy is not None else "No quiz yet"
        await update.effective_message.reply_text(
            "📊 YOUR LAST 7 DAYS\n\n"
            f"Questions completed: {progress.questions_solved}\n"
            f"Practice sessions: {progress.practice_completed}\n"
            f"Quiz accuracy: {accuracy}\n"
            f"Current streak: {progress.current_streak} days\n"
            f"Longest streak: {progress.longest_streak} days\n"
            f"Study days: {progress.study_days} / 7\n"
            f"Most studied: {progress.most_studied_topic or 'Not enough data'}\n"
            f"Weakest topic: {progress.weakest_topic or 'Not enough quiz data'}"
        )

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        message = update.effective_message
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
            payload.image_bytes = await self._download_file(context, photo.file_id)
            payload.image_mime_type = "image/jpeg"
            payload.filename = "telegram-photo.jpg"
        elif message.document:
            document = message.document
            if not await self._check_size(update, document.file_size):
                return
            mime_type = (document.mime_type or "").lower()
            filename = document.file_name or "document"
            if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
                payload.pdf_bytes = await self._download_file(context, document.file_id)
                payload.filename = filename
            elif mime_type.startswith("image/"):
                payload.image_bytes = await self._download_file(context, document.file_id)
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

        if payload.has_attachment:
            data = payload.image_bytes if payload.has_image else payload.pdf_bytes
            mime = payload.image_mime_type if payload.has_image else "application/pdf"
            payload.file_reference = await self._database.upload_file(
                user_id=profile.id,
                filename=payload.filename or "upload.bin",
                mime_type=mime or "application/octet-stream",
                data=data or b"",
            )

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
            context.user_data["pending_request"] = {
                "text": payload.text,
                "file_reference": payload.file_reference,
                "filename": payload.filename,
                "image_mime_type": payload.image_mime_type,
                "is_pdf": payload.has_pdf,
                "ignore_history": ignore_history,
            }
            await message.reply_text(
                "What would you like me to do with this?",
                reply_markup=clarification_keyboard(),
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
                await message.reply_text(f"Please wait {wait} second before sending another AI request.")
                return None
            usage = await self._database.consume_usage(
                profile, self._settings.daily_ai_request_limit
            )
            if not usage.allowed:
                await message.reply_text(
                    f"You have used today's {usage.limit} BuildWise AI requests. "
                    "Your limit resets at midnight in your saved timezone."
                )
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
                        logger.warning("Could not remove failed-request upload")
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
                    logger.exception("Answer succeeded but history save failed")
                    if payload.file_reference:
                        try:
                            await self._database.delete_file(payload.file_reference)
                            payload.file_reference = None
                        except SupabaseError:
                            logger.warning("Could not clean up an unsaved upload")
            elif payload.file_reference:
                try:
                    await self._database.delete_file(payload.file_reference)
                    payload.file_reference = None
                except SupabaseError:
                    logger.warning("Could not clean up a history-disabled upload")

            context.user_data.update(
                {
                    "last_mode": mode.value,
                    "last_question": prompt_text,
                    "last_answer": answer,
                    "last_file_reference": payload.file_reference,
                    "last_conversation_id": conversation_id,
                }
            )
            formatted = format_response(mode, answer)
            chunks = split_for_telegram(formatted)
            markup = follow_up_keyboard(mode) if profile.follow_up_buttons else None
            if mode is Mode.SKETCH:
                report_key = str(uuid4())
                reports = context.user_data.setdefault("sketch_reports", {})
                reports[report_key] = {"answer": answer, "image": payload.image_bytes}
                while len(reports) > 2:
                    reports.pop(next(iter(reports)))
                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Get PDF", callback_data=f"sketchpdf:{report_key}")],
                    *(markup.inline_keyboard if markup else []),
                ])
                chunks = [format_response(mode, sketch_preview(answer))]
            try:
                await loading.edit_text(
                    chunks[0], reply_markup=markup if len(chunks) == 1 else None
                )
            except BadRequest:
                await message.reply_text(
                    chunks[0], reply_markup=markup if len(chunks) == 1 else None
                )
            for index, chunk in enumerate(chunks[1:], start=1):
                await message.reply_text(
                    chunk, reply_markup=markup if index == len(chunks) - 1 else None
                )

            if mode is Mode.STUDY and activity_type:
                try:
                    completion = await self._database.record_study_completion(
                        profile,
                        activity_type=activity_type,
                        subject=None,
                        topic=topic or self._trim(prompt_text, 100),
                        units=1,
                    )
                    await self._celebrate(update, completion)
                except SupabaseError:
                    logger.exception("Could not record study completion")
            return answer

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        profile = await self._profile(update)
        data = query.data or ""

        if data.startswith("home:"):
            await self._handle_home_callback(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("mode:"):
            raw = data.split(":", 1)[1]
            if raw == "auto":
                context.user_data.pop("mode", None)
                await query.message.reply_text("✨ Automatic detection is active.")
            else:
                await self._activate_mode(update, context, Mode(raw))
            return
        if data.startswith("route:"):
            await self._handle_route_choice(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("settings:"):
            await self._handle_settings_callback(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("goal:"):
            value = data.split(":", 1)[1]
            if value == "custom":
                context.user_data["awaiting_custom_goal"] = True
                await query.message.reply_text("Send your custom daily goal (1 to 100).")
            else:
                await self._set_goal(update, profile, value)
            return
        if data.startswith("reminder:"):
            await self._handle_reminder_callback(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("followup:"):
            await self._handle_followup(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("history:"):
            await self._handle_history_callback(update, context, profile, data.split(":", 1)[1])
            return
        if data.startswith("practice:"):
            await self._handle_practice_callback(update, context, profile, data)

    async def _handle_home_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action == "menu":
            await self._show_home(update, profile)
        elif action == "study":
            await self._activate_mode(update, context, Mode.STUDY)
        elif action == "sketch":
            await self._activate_mode(update, context, Mode.SKETCH)
        elif action == "building":
            await self._activate_mode(update, context, Mode.BUILDING)
        elif action == "practice":
            await self._start_practice(update, context)
        elif action == "history":
            await self._show_history(update, profile)
        elif action == "settings":
            await self._show_settings(update, profile)
        elif action == "continue":
            rows = await self._database.recent_conversations(profile.id, limit=1)
            if rows:
                mode = Mode(rows[0]["mode"])
                context.user_data["mode"] = mode.value
                await update.effective_message.reply_text(
                    f"Continuing in {mode.label}. Send your next question or file."
                )
            else:
                await self._activate_mode(update, context, Mode.STUDY)

    async def _handle_route_choice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        choice: str,
    ) -> None:
        pending = context.user_data.pop("pending_request", None)
        if not pending:
            await update.effective_message.reply_text(
                "That upload is no longer pending. Please send it again."
            )
            return
        mode = Mode.STUDY if choice in {"study", "explain"} else Mode(choice)
        reference = pending.get("file_reference")
        try:
            file_data = await self._database.download_file(reference) if reference else None
        except SupabaseError:
            await update.effective_message.reply_text(
                "I could not reopen that upload. Please send the file again."
            )
            return
        payload = RequestPayload(
            text=pending.get("text") or "",
            filename=pending.get("filename"),
            file_reference=reference,
        )
        if pending.get("is_pdf"):
            payload.pdf_bytes = file_data
        else:
            payload.image_bytes = file_data
            payload.image_mime_type = pending.get("image_mime_type") or "image/jpeg"
        await self._respond(
            update,
            context,
            profile,
            mode,
            payload,
            activity_type="solve" if mode is Mode.STUDY else None,
            ignore_history=bool(pending.get("ignore_history", False)),
        )

    async def _handle_settings_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action in {"style", "buttons", "history"}:
            profile = await self._database.toggle_user_setting(profile, action)
            await update.callback_query.edit_message_text(
                self._settings_text(profile), reply_markup=settings_keyboard(profile)
            )
        elif action == "timezone":
            context.user_data["awaiting_timezone"] = True
            await update.effective_message.reply_text(
                "Send a timezone such as Africa/Lagos, Europe/London, or America/Chicago."
            )
        elif action == "goal":
            await update.effective_message.reply_text(
                "Choose your questions-per-day goal:", reply_markup=goal_keyboard()
            )
        elif action == "reminders":
            if not profile.timezone_confirmed:
                await update.effective_message.reply_text(
                    "Set your timezone first with /timezone Area/City."
                )
            else:
                await update.effective_message.reply_text(
                    f"Choose one reminder time in {profile.timezone}:",
                    reply_markup=reminder_keyboard(),
                )

    async def _handle_reminder_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action == "off":
            await self._database.set_reminder(profile, enabled=False)
            await update.effective_message.reply_text("🔕 Study reminders are off.")
        elif action == "custom":
            context.user_data["awaiting_reminder_time"] = True
            await update.effective_message.reply_text(
                "Send a local time in 24-hour HH:MM format, for example 18:30."
            )
        elif action == "snooze":
            await self._database.snooze_reminder(profile.id, hours=2)
            await update.effective_message.reply_text("I will remind you again in about 2 hours.")
        else:
            await self._database.set_reminder(
                profile, enabled=True, reminder_time=action
            )
            await update.effective_message.reply_text(
                f"🔔 Daily reminder set for {action} in {profile.timezone}."
            )

    async def _set_custom_reminder(
        self, update: Update, profile: UserProfile, raw_time: str
    ) -> None:
        try:
            parsed = time.fromisoformat(raw_time.strip())
        except ValueError:
            await update.effective_message.reply_text(
                "Use 24-hour HH:MM format, for example 18:30."
            )
            return
        value = parsed.strftime("%H:%M")
        await self._database.set_reminder(profile, enabled=True, reminder_time=value)
        await update.effective_message.reply_text(
            f"🔔 Daily reminder set for {value} in {profile.timezone}."
        )

    async def _handle_followup(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        last = await self._last_context(context, profile)
        if action == "new":
            context.user_data["ignore_history_once"] = True
            await update.effective_message.reply_text("Send your next question or file.")
            return
        if not last:
            await update.effective_message.reply_text("Ask a question first.")
            return
        mode, question, answer = last
        if action == "check":
            context.user_data["awaiting_check_answer"] = True
            context.user_data["check_context"] = {
                "mode": mode.value,
                "question": question,
                "answer": answer,
            }
            await update.effective_message.reply_text("Send your answer and I will check it.")
            return
        prompts = {
            "steps": "Show the full reasoning or steps for the previous answer.",
            "simpler": "Explain the previous answer using simpler words and one short example.",
            "similar": "Create one similar practice question. Do not show its answer yet.",
        }
        if action not in prompts:
            return
        await self._respond(
            update,
            context,
            profile,
            mode,
            RequestPayload(text=prompts[action]),
            activity_type=None,
            extra_history=[
                ConversationTurn("user", question),
                ConversationTurn("assistant", answer),
            ],
        )

    async def _check_student_answer(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        proposed_answer: str,
    ) -> None:
        check = context.user_data.pop("check_context", None)
        if not check:
            await update.effective_message.reply_text(
                "I no longer have the question to check. Please send it again."
            )
            return
        mode = Mode(check["mode"])
        prompt = (
            f"The student answered: {proposed_answer}\n\n"
            "Determine the correct result and check it against the previous question. "
            "Start with Correct, "
            "Partly correct, or Incorrect, then give one concise correction."
        )
        await self._respond(
            update,
            context,
            profile,
            mode,
            RequestPayload(text=prompt),
            activity_type="practice" if mode is Mode.STUDY else None,
            extra_history=[
                ConversationTurn("user", check["question"]),
                ConversationTurn("assistant", check["answer"]),
            ],
        )

    async def _handle_history_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action == "save":
            last = await self._last_context(context, profile)
            if not last:
                await update.effective_message.reply_text("There is no recent answer to save.")
                return
            mode, question, answer = last
            await self._database.save_item(
                user_id=profile.id,
                item_type=mode.value,
                title=self._trim(question, 100),
                content=answer,
                file_reference=context.user_data.get("last_file_reference"),
            )
            await update.effective_message.reply_text("Saved to BuildWise History.")
        elif action == "delete":
            await update.effective_message.reply_text(
                "Delete all history, saved items, and stored files?",
                reply_markup=history_confirm_keyboard(),
            )
        elif action == "confirm_delete":
            count = await self._database.delete_history(profile.id)
            self._clear_learning_context(context)
            await update.effective_message.reply_text(
                f"History deleted. {count} conversation item(s) removed."
            )
        elif action == "cancel_delete":
            await update.effective_message.reply_text("History was not deleted.")

    async def _handle_practice_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        data: str,
    ) -> None:
        parts = data.split(":")
        if len(parts) >= 3 and parts[1] == "difficulty":
            setup = context.user_data.get("practice_setup")
            if not setup:
                await update.effective_message.reply_text("Start again with /practice.")
                return
            setup["difficulty"] = parts[2]
            await update.effective_message.reply_text(
                "How many questions?", reply_markup=practice_count_keyboard()
            )
            return
        if len(parts) >= 3 and parts[1] == "count":
            await self._generate_practice(update, context, profile, int(parts[2]))
            return
        if len(parts) >= 3 and parts[1] == "answer":
            await self._answer_practice_question(update, context, int(parts[2]))
            return
        if len(parts) >= 2 and parts[1] == "next":
            await self._next_practice(update, context, profile)

    async def _generate_practice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        count: int,
    ) -> None:
        setup = context.user_data.get("practice_setup")
        if not setup or not setup.get("difficulty") or count not in {3, 5, 10}:
            await update.effective_message.reply_text("Start again with /practice.")
            return
        if context.user_data.get("practice_generating"):
            await update.effective_message.reply_text("Your practice is already being prepared.")
            return
        context.user_data["practice_generating"] = True
        usage = await self._database.consume_usage(
            profile, self._settings.daily_ai_request_limit
        )
        if not usage.allowed:
            context.user_data.pop("practice_generating", None)
            await update.effective_message.reply_text(
                f"You have used today's {usage.limit} BuildWise AI requests."
            )
            return
        loading = await update.effective_message.reply_text("🧠 Building your practice...")
        try:
            questions = await self._ai.generate_quiz(
                topic=setup["topic"], difficulty=setup["difficulty"], count=count
            )
        except AIServiceError as exc:
            await self._database.refund_usage(profile)
            context.user_data.pop("practice_generating", None)
            await loading.edit_text(exc.public_message)
            return
        context.user_data["practice_session"] = {
            "topic": setup["topic"],
            "difficulty": setup["difficulty"],
            "questions": questions,
            "index": 0,
            "correct": 0,
            "answered": False,
            "mistakes": [],
        }
        context.user_data.pop("practice_setup", None)
        context.user_data.pop("practice_generating", None)
        await loading.edit_text("Practice ready.")
        await self._send_practice_question(update, context)

    async def _send_practice_question(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        session = context.user_data.get("practice_session")
        if not session:
            return
        questions: list[QuizQuestion] = session["questions"]
        index = int(session["index"])
        question = questions[index]
        letters = ("A", "B", "C", "D")
        options = "\n".join(
            f"{letters[i]}. {option}" for i, option in enumerate(question.options)
        )
        await update.effective_message.reply_text(
            f"🧠 Question {index + 1} / {len(questions)}\n\n"
            f"{question.question}\n\n{options}",
            reply_markup=quiz_answer_keyboard(),
        )

    async def _answer_practice_question(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, selected: int
    ) -> None:
        session = context.user_data.get("practice_session")
        if not session:
            await update.effective_message.reply_text("Use /practice to start a new session.")
            return
        if session.get("answered"):
            # Telegram can deliver repeated taps on the same inline answer button.
            # Ignore them instead of telling the student that the active session vanished.
            return
        questions: list[QuizQuestion] = session["questions"]
        question = questions[int(session["index"])]
        session["answered"] = True
        correct = selected == question.correct_index
        if correct:
            session["correct"] += 1
            result = "✅ Correct"
        else:
            session["mistakes"].append(
                {
                    "question": question.question,
                    "correct": question.options[question.correct_index],
                }
            )
            result = (
                "❌ Not quite\nCorrect answer: "
                f"{chr(65 + question.correct_index)}. {question.options[question.correct_index]}"
            )
        finished = int(session["index"]) == len(questions) - 1
        await update.effective_message.reply_text(
            f"{result}\n\n{question.explanation}",
            reply_markup=quiz_next_keyboard(finished=finished),
        )

    async def _next_practice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
    ) -> None:
        session = context.user_data.get("practice_session")
        if not session or not session.get("answered"):
            await update.effective_message.reply_text("Choose an answer first.")
            return
        questions: list[QuizQuestion] = session["questions"]
        if int(session["index"]) < len(questions) - 1:
            session["index"] += 1
            session["answered"] = False
            await self._send_practice_question(update, context)
            return

        correct = int(session["correct"])
        total = len(questions)
        score = round(100 * correct / total)
        await self._database.record_quiz_result(
            user_id=profile.id,
            subject=None,
            topic=session["topic"],
            total_questions=total,
            correct_answers=correct,
        )
        completion = await self._database.record_study_completion(
            profile,
            activity_type="quiz",
            subject=None,
            topic=session["topic"],
            units=total,
        )
        mistakes = session["mistakes"]
        if mistakes:
            mistake_text = "\n".join(
                f"• {self._trim(item['question'], 90)} → {item['correct']}"
                for item in mistakes[:5]
            )
        else:
            mistake_text = "None — excellent work."
        suggestion = (
            f"Review {session['topic']} and try 3 easier questions."
            if score < 70
            else f"Try a harder {session['topic']} set next."
        )
        await update.effective_message.reply_text(
            "🏁 PRACTICE COMPLETE\n\n"
            f"Score: {correct} / {total} ({score}%)\n\n"
            f"Mistakes:\n{mistake_text}\n\n"
            f"Suggested next step: {suggestion}"
        )
        await self._celebrate(update, completion)
        context.user_data.pop("practice_session", None)

    async def _last_context(
        self, context: ContextTypes.DEFAULT_TYPE, profile: UserProfile
    ) -> tuple[Mode, str, str] | None:
        question = str(context.user_data.get("last_question", "")).strip()
        answer = str(context.user_data.get("last_answer", "")).strip()
        raw_mode = context.user_data.get("last_mode")
        if question and answer and raw_mode:
            return Mode(raw_mode), question, answer
        rows = await self._database.recent_conversations(profile.id, limit=1)
        if not rows:
            return None
        return Mode(rows[0]["mode"]), str(rows[0]["message"]), str(rows[0]["response"])

    async def _celebrate(self, update: Update, completion: StudyCompletion) -> None:
        if completion.milestone:
            descriptions = {
                3: "Three consistent study days.",
                7: "You've studied for one full week.",
                14: "Two weeks of steady learning.",
                30: "A full month of consistency.",
                50: "Fifty study days — excellent discipline.",
                100: "One hundred study days — a major achievement.",
            }
            await update.effective_message.reply_text(
                f"🔥 {completion.milestone} Days!\n\n"
                f"{descriptions[completion.milestone]} Keep going."
            )
        if completion.goal_just_completed:
            await update.effective_message.reply_text(
                f"🎯 Daily goal complete: {completion.completed_today} / "
                f"{completion.daily_target}."
            )

    async def _profile(self, update: Update) -> UserProfile:
        user = update.effective_user
        if user is None:
            raise SupabaseError("Telegram user information is missing")
        return await self._database.upsert_user(
            user.id, user.username, user.full_name or "Student"
        )

    async def _download_file(
        self, context: ContextTypes.DEFAULT_TYPE, file_id: str
    ) -> bytes:
        telegram_file = await context.bot.get_file(file_id)
        return bytes(await telegram_file.download_as_bytearray())

    async def _check_size(self, update: Update, file_size: int | None) -> bool:
        if file_size is not None and file_size > self._settings.max_file_size_bytes:
            await update.effective_message.reply_text(
                f"That file is too large. The BuildWise limit is "
                f"{self._settings.max_file_size_mb} MB."
            )
            return False
        return True

    def _selected_mode(self, context: ContextTypes.DEFAULT_TYPE) -> Mode | None:
        raw = context.user_data.get("mode")
        try:
            return Mode(raw) if raw else None
        except ValueError:
            context.user_data.pop("mode", None)
            return None

    @staticmethod
    def _caption_command(text: str) -> str | None:
        if not text.startswith("/"):
            return None
        return text[1:].partition(" ")[0].split("@", 1)[0].lower()

    @classmethod
    def _extract_caption_mode(cls, text: str) -> tuple[Mode | None, str]:
        command = cls._caption_command(text)
        if command is None:
            return None, text
        _, _, remainder = text.partition(" ")
        return MODE_ALIASES.get(command), remainder.strip()

    @staticmethod
    def _loading_text(mode: Mode) -> str:
        return {
            Mode.STUDY: "🧠 Solving your question...",
            Mode.BUILDING: "🔍 Analyzing your building photo...",
            Mode.SKETCH: "📐 Reading your sketch...",
        }[mode]

    @staticmethod
    def _attachment_label(payload: RequestPayload) -> str:
        kind = "PDF" if payload.has_pdf else "image"
        filename = f" ({payload.filename})" if payload.filename else ""
        return f"Uploaded {kind}{filename} for analysis."

    @staticmethod
    def _settings_text(profile: UserProfile) -> str:
        return (
            "⚙️ BUILDWISE SETTINGS\n\n"
            f"Answer style: {profile.answer_style.value.title()}\n"
            f"Follow-up buttons: {'On' if profile.follow_up_buttons else 'Off'}\n"
            f"Save history: {'On' if profile.save_history else 'Off'}\n"
            f"Timezone: {profile.timezone}"
            f"{' ✓' if profile.timezone_confirmed else ' (confirm this)'}\n"
            f"Daily goal: {profile.default_daily_goal}"
        )

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        cleaned = " ".join(text.split())
        return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."

    @staticmethod
    def _clear_learning_context(context: ContextTypes.DEFAULT_TYPE) -> None:
        for key in (
            "last_mode",
            "last_question",
            "last_answer",
            "last_file_reference",
            "last_conversation_id",
            "check_context",
            "practice_session",
            "pending_request",
        ):
            context.user_data.pop(key, None)

    async def handle_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        error = context.error
        if isinstance(error, SupabaseError):
            public_message = (
                "BuildWise could not reach its saved data right now. Please try again shortly."
            )
        elif isinstance(error, TelegramError):
            public_message = "Telegram could not complete that action. Please try again."
        else:
            public_message = "Something unexpected went wrong. Please try again."
        logger.error("Unhandled BuildWise update error", exc_info=error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(public_message)
            except TelegramError:
                logger.exception("Could not send the friendly error message")
