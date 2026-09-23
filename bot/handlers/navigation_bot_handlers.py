"""Role-aware Telegram navigation layered on the existing student handlers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, time as dt_time, timedelta, timezone
from html import escape
import logging
import os
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
)

from bot.domain import ConversationTurn, Mode, RequestPayload, UserProfile
from bot.formatting import format_response, split_for_telegram, sketch_preview
from bot.handlers.bot_handlers import BotHandlers
from bot.keyboards import follow_up_keyboard
from bot.pro.beta_config import validated_admin_telegram_ids, validated_pro_allowlist
from bot.services.openai_service import AIServiceError
from bot.services.reward_usage_service import RewardAwareSupabaseService
from bot.services.supabase_service import SupabaseError
from bot.study_experience import StudyRequestKind, detect_study_request_kind

logger = logging.getLogger(__name__)

_current_study_request_kind: ContextVar[StudyRequestKind] = ContextVar(
    "buildwise_study_request_kind", default=StudyRequestKind.TOPIC
)


def _parse_optional_ids(raw: str, variable: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    values: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{variable} must contain numeric Telegram IDs") from exc
        if parsed <= 0:
            raise ValueError(f"{variable} must contain positive Telegram IDs")
        values.add(parsed)
    return frozenset(values)


def navigation_home_keyboard(
    *,
    timezone_confirmed: bool,
    pro_access: bool,
    admin_access: bool,
) -> InlineKeyboardMarkup:
    """Build the home menu without exposing private controls to unauthorized users."""
    rows = [
        [InlineKeyboardButton("🎓 Ask BuildWise", callback_data="home:study")],
        [
            InlineKeyboardButton("🧠 Practice", callback_data="home:practice"),
            InlineKeyboardButton("📄 Analyze PDF", callback_data="home:pdf"),
        ],
        [
            InlineKeyboardButton("📐 Analyze Sketch", callback_data="home:sketch"),
            InlineKeyboardButton("🏗 Analyze Building", callback_data="home:building"),
        ],
        [InlineKeyboardButton("🎯 Study Tools", callback_data="home:studytools")],
        [
            InlineKeyboardButton("🛟 Support", callback_data="nav:support"),
            InlineKeyboardButton("🆔 My ID", callback_data="nav:myid"),
        ],
    ]
    if pro_access:
        rows.append(
            [
                InlineKeyboardButton("🏗 BuildWise Pro", callback_data="nav:pro"),
                InlineKeyboardButton("📊 Pro Status", callback_data="nav:pro_status"),
            ]
        )
    if admin_access:
        rows.append(
            [InlineKeyboardButton("📈 Beta Stats", callback_data="nav:pro_beta_stats")]
        )
    if not timezone_confirmed:
        rows.append(
            [InlineKeyboardButton("🌍 Set timezone", callback_data="settings:timezone")]
        )
    return InlineKeyboardMarkup(rows)


def study_tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📂 History", callback_data="home:history"),
                InlineKeyboardButton("📊 Progress", callback_data="home:progress"),
            ],
            [
                InlineKeyboardButton("🎯 Daily Goal", callback_data="settings:goal"),
                InlineKeyboardButton("🔔 Reminders", callback_data="settings:reminders"),
            ],
            [InlineKeyboardButton("⚙️ Settings", callback_data="home:settings")],
            [InlineKeyboardButton("🏠 Back to Home", callback_data="home:menu")],
        ]
    )


class NavigationBotHandlers(BotHandlers):
    """Preserve student behavior while adding a simpler, role-aware home surface."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pro_navigation_ids = validated_pro_allowlist(self._settings)
        self._admin_navigation_ids = validated_admin_telegram_ids()
        notify_ids = _parse_optional_ids(
            os.getenv("BUILDWISE_ADMIN_NOTIFY_TELEGRAM_IDS", ""),
            "BUILDWISE_ADMIN_NOTIFY_TELEGRAM_IDS",
        )
        self._admin_notify_ids = notify_ids | self._admin_navigation_ids

    def register(self, application: Application) -> None:
        super().register(application)
        application.add_handler(CommandHandler("menu", self.home_command))

    async def post_init(self, application: Application) -> None:
        await super().post_init(application)
        commands = list(await application.bot.get_my_commands())
        additions = [
            BotCommand("menu", "Open the BuildWise menu"),
            BotCommand("myid", "Show your Telegram numeric ID"),
        ]
        existing = {command.command for command in commands}
        for command in additions:
            if command.command not in existing:
                commands.append(command)
        await application.bot.set_my_commands(commands)

    async def guard_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await super().guard_update(update, context)
        message = update.effective_message
        raw_text = (message.text or message.caption or "").strip() if message else ""
        command = raw_text.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw_text.startswith("/") else ""
        if command == "/start":
            return

        profile = await self._profile(update)
        identity = await self._student_identity(context, profile)
        if identity is not None:
            return

        if message is not None and context.user_data.get("onboarding_step"):
            await self._handle_onboarding_input(update, context, profile, raw_text)
            raise ApplicationHandlerStop

        if update.callback_query is not None:
            try:
                await update.callback_query.answer("Complete your BuildWise setup first.")
            except TelegramError:
                pass
        context.user_data["onboarding_step"] = "name"
        if message is not None:
            await message.reply_text(
                "👋 Before you start using BuildWise, I need two quick details.\n\n"
                "What name should I call you?"
            )
        raise ApplicationHandlerStop

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        identity = await self._student_identity(context, profile)
        if identity is None:
            context.user_data["onboarding_step"] = "name"
            await update.effective_message.reply_text(
                "👋 Welcome to BuildWise AI.\n\n"
                "Before we begin, I need two quick details so your learning profile is useful.\n\n"
                "1️⃣ What name should I call you?"
            )
            return
        await super().start(update, context)

    async def _student_identity(
        self, context: ContextTypes.DEFAULT_TYPE, profile: UserProfile
    ) -> dict[str, str] | None:
        cached = context.user_data.get("_student_identity")
        if isinstance(cached, dict) and cached.get("name") and cached.get("university"):
            return {"name": str(cached["name"]), "university": str(cached["university"])}
        database = self._database
        if not isinstance(database, RewardAwareSupabaseService):
            return {"name": profile.display_name, "university": "Not specified"}
        identity = await database.get_student_identity(profile.id)
        if identity:
            context.user_data["_student_identity"] = identity
        return identity

    async def _handle_onboarding_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        raw_text: str,
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        value = " ".join(raw_text.split()).strip()
        step = str(context.user_data.get("onboarding_step") or "name")

        if step == "name":
            if not 2 <= len(value) <= 60 or value.startswith("/"):
                await message.reply_text("Please send the name you want BuildWise to call you.")
                return
            context.user_data["onboarding_name"] = value
            context.user_data["onboarding_step"] = "university"
            await message.reply_text(
                f"Nice to meet you, {value}. 👋\n\n"
                "2️⃣ What university, polytechnic, college, or institution do you attend?"
            )
            return

        name = str(context.user_data.get("onboarding_name") or "").strip()
        if not name:
            context.user_data["onboarding_step"] = "name"
            await message.reply_text("First, send the name you want BuildWise to call you.")
            return
        if not 2 <= len(value) <= 140 or value.startswith("/"):
            await message.reply_text("Please send the full name of your institution.")
            return

        database = self._database
        if isinstance(database, RewardAwareSupabaseService):
            await database.save_student_identity(profile.id, name=name, university=value)
        profile = await self._database.update_user(profile.id, {"display_name": name})
        identity = {"name": name, "university": value}
        context.user_data["_student_identity"] = identity
        context.user_data.pop("onboarding_name", None)
        context.user_data.pop("onboarding_step", None)

        await message.reply_text(
            "✅ Setup complete. Welcome to BuildWise AI.\n\n"
            "You can now ask construction questions, solve calculations, analyze PDFs, "
            "work with sketches and floor plans, or practice quizzes."
        )
        await self._notify_admin_new_student(context, update, identity)
        await self._show_home(update, profile)
        if not profile.timezone_confirmed and not profile.timezone_prompted:
            await message.reply_text(
                "🌍 One last optional setup: send /timezone followed by your area, for example "
                "/timezone Africa/Lagos. This gives you the exact local reset time for daily limits, streaks and reminders."
            )
            await self._database.mark_timezone_prompted(profile.id)

    async def _notify_admin_new_student(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        update: Update,
        identity: dict[str, str],
    ) -> None:
        if not self._admin_notify_ids:
            return
        user = update.effective_user
        if user is None:
            return
        username = f"@{user.username}" if user.username else "No username"
        text = (
            "🆕 New BuildWise student\n\n"
            f"Name: {identity['name']}\n"
            f"Institution: {identity['university']}\n"
            f"Telegram: {username}\n"
            f"Telegram ID: {user.id}"
        )
        for admin_id in self._admin_notify_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except TelegramError:
                logger.warning("Could not send new-user admin notification admin_id=%s", admin_id)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        text = (message.text or message.caption or "") if message is not None else ""
        token = _current_study_request_kind.set(detect_study_request_kind(text))
        try:
            await super().handle_message(update, context)
        finally:
            _current_study_request_kind.reset(token)

    async def solve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = " ".join(context.args).strip()
        token = _current_study_request_kind.set(detect_study_request_kind(text))
        try:
            await super().solve(update, context)
        finally:
            _current_study_request_kind.reset(token)

    async def study(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = " ".join(context.args).strip()
        token = _current_study_request_kind.set(detect_study_request_kind(text))
        try:
            await super().study(update, context)
        finally:
            _current_study_request_kind.reset(token)

    async def _handle_followup(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        last = await self._last_context(context, profile)
        if action == "copy":
            if not last:
                await update.effective_message.reply_text("Ask a question first.")
                return
            mode, _, answer = last
            copy_ready = format_response(mode, answer)
            await update.effective_message.reply_text("📋 Copy-ready answer:")
            for chunk in split_for_telegram(copy_ready, limit=3500):
                await update.effective_message.reply_text(
                    f"<pre>{escape(chunk)}</pre>", parse_mode="HTML"
                )
            return

        question = last[1] if last else ""
        token = _current_study_request_kind.set(detect_study_request_kind(question))
        try:
            await super()._handle_followup(update, context, profile, action)
        finally:
            _current_study_request_kind.reset(token)

    @staticmethod
    def _loading_text(mode: Mode) -> str:
        if mode is Mode.STUDY:
            if _current_study_request_kind.get() is StudyRequestKind.CALCULATION:
                return "🧮 Solving calculations..."
            return "📚 Explaining topic..."
        return {
            Mode.BUILDING: "🔍 Analyzing your building photo...",
            Mode.SKETCH: "📐 Reading your sketch...",
        }[mode]

    @staticmethod
    def _reset_message(profile: UserProfile, limit: int) -> str:
        try:
            zone = ZoneInfo(profile.timezone)
            zone_label = profile.timezone
        except (ZoneInfoNotFoundError, ValueError):
            zone = timezone.utc
            zone_label = "UTC"
        now = datetime.now(timezone.utc).astimezone(zone)
        reset = datetime.combine(
            now.date() + timedelta(days=1), dt_time.min, tzinfo=zone
        )
        remaining = max(timedelta(0), reset - now)
        total_minutes = int(remaining.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        reset_label = reset.strftime("%I:%M %p on %A, %d %B").lstrip("0")
        timezone_note = "" if profile.timezone_confirmed else "\nSet /timezone to use your own local reset time."
        return (
            f"You've used today's {limit} BuildWise AI requests.\n\n"
            f"⏰ Your limit resets at {reset_label} ({zone_label}) — in about {hours}h {minutes}m."
            f"{timezone_note}"
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
                markup = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("📋 Copy", callback_data="followup:copy"),
                        InlineKeyboardButton("📄 PDF", callback_data=f"sketchpdf:{report_key}"),
                    ]]
                )
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
            await update.effective_message.reply_text(self._reset_message(profile, usage.limit))
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
            "id": uuid4().hex[:8],
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

    async def _show_home(self, update: Update, profile: UserProfile) -> None:
        stats = await self._database.get_dashboard(profile)
        streak_label = "day" if stats.current_streak == 1 else "days"
        user = update.effective_user
        user_id = user.id if user is not None else None
        identity = None
        try:
            database = self._database
            if isinstance(database, RewardAwareSupabaseService):
                identity = await database.get_student_identity(profile.id)
        except SupabaseError:
            identity = None
        greeting = f"Hi {identity['name']}, " if identity else ""
        text = (
            f"🏠 {greeting}BuildWise\n"
            "Study, analyze and work with project material.\n\n"
            f"🎯 Today: {stats.completed_today} / {stats.daily_target}  •  "
            f"🔥 Streak: {stats.current_streak} {streak_label}\n\n"
            "Choose an option below, or send a message, image, PDF or sketch directly."
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=navigation_home_keyboard(
                timezone_confirmed=profile.timezone_confirmed,
                pro_access=(
                    self._settings.buildwise_pro_enabled
                    and user_id in self._pro_navigation_ids
                ),
                admin_access=user_id in self._admin_navigation_ids,
            ),
        )

    async def _handle_home_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action == "pdf":
            context.user_data["mode"] = "study"
            await update.effective_message.reply_text(
                "📄 Send the PDF you want analyzed. You can include a question or instruction with it."
            )
            return
        if action == "studytools":
            await update.effective_message.reply_text(
                "🎯 Study tools\n\nChoose what you want to manage:",
                reply_markup=study_tools_keyboard(),
            )
            return
        if action == "progress":
            await self.progress_command(update, context)
            return
        await super()._handle_home_callback(update, context, profile, action)
