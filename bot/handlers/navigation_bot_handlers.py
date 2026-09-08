"""Role-aware Telegram navigation layered on the existing student handlers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta
import logging
from time import monotonic
from zoneinfo import ZoneInfo

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.domain import ConversationTurn, Mode, RequestPayload, UserProfile
from bot.formatting import format_response, split_for_telegram
from bot.handlers.bot_handlers import BotHandlers
from bot.keyboards import follow_up_keyboard
from bot.pro.beta_config import validated_admin_telegram_ids, validated_pro_allowlist
from bot.services.openai_service import AIServiceError
from bot.services.supabase_service import SupabaseError
from bot.study_experience import StudyRequestKind, detect_study_request_kind

logger = logging.getLogger(__name__)

_current_study_request_kind: ContextVar[StudyRequestKind] = ContextVar(
    "buildwise_study_request_kind", default=StudyRequestKind.TOPIC
)


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
            InlineKeyboardButton("📝 Practice", callback_data="home:practice"),
            InlineKeyboardButton("📄 Analyze PDF", callback_data="home:pdf"),
        ],
        [
            InlineKeyboardButton("🖼 Sketch / Image", callback_data="home:sketch"),
            InlineKeyboardButton("🏗 Building Photo", callback_data="home:building"),
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
        self._join_notification_ids = (
            self._admin_navigation_ids | self._settings.join_notification_telegram_ids
        )

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

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
        await self._show_home(update, profile)
        await self._offer_timezone_setup(update, profile)

    async def home_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._require_onboarding(update, context)
        if profile is None:
            return
        await self._show_home(update, profile)

    async def practice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._require_onboarding(update, context)
        if profile is None:
            return
        await super().practice(update, context)

    async def _mode_command_with_optional_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        mode: Mode,
        *,
        activity_type: str | None = None,
    ) -> None:
        profile = await self._require_onboarding(update, context)
        if profile is None:
            return
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

    async def _require_onboarding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> UserProfile | None:
        profile = await self._profile(update)
        if profile.onboarding_completed:
            return profile
        await self._begin_onboarding(update, context)
        return None

    async def _begin_onboarding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        context.user_data["onboarding_stage"] = "name"
        context.user_data.pop("onboarding_name", None)
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "👋 Welcome to BuildWise AI.\n\n"
                "Before we start, I need two quick details so your study experience is more personal.\n\n"
                "1️⃣ What name should I call you?\n"
                "Send your name below."
            )

    async def _handle_onboarding_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        text: str,
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        stage = str(context.user_data.get("onboarding_stage") or "name")
        cleaned = " ".join(text.split())

        if stage == "name":
            if not 2 <= len(cleaned) <= 80 or cleaned.startswith("/"):
                await message.reply_text(
                    "Please send the name you want BuildWise to call you (2–80 characters)."
                )
                return
            context.user_data["onboarding_name"] = cleaned
            context.user_data["onboarding_stage"] = "university"
            await message.reply_text(
                f"Nice to meet you, {cleaned}. 👋\n\n"
                "2️⃣ What university, polytechnic, college, or other institution do you attend?\n"
                "Please send the full institution name."
            )
            return

        if stage == "university":
            if not 2 <= len(cleaned) <= 160 or cleaned.startswith("/"):
                await message.reply_text(
                    "Please send your institution name (2–160 characters)."
                )
                return
            student_name = str(context.user_data.get("onboarding_name") or "").strip()
            if not student_name:
                await self._begin_onboarding(update, context)
                return
            completed = await self._database.update_user(
                profile.id,
                {
                    "student_name": student_name,
                    "university": cleaned,
                    "onboarding_completed": True,
                    "onboarding_completed_at": datetime.now().astimezone().isoformat(),
                },
            )
            context.user_data.pop("onboarding_stage", None)
            context.user_data.pop("onboarding_name", None)
            await self._notify_admins_of_join(context, completed)
            await message.reply_text(
                f"✅ Setup complete, {student_name}.\n\n"
                "BuildWise is ready. Ask a construction question, solve a calculation, "
                "upload a PDF, practice a quiz, or analyze a sketch/image."
            )
            await self._show_home(update, completed)
            await self._offer_timezone_setup(update, completed)

    async def _notify_admins_of_join(
        self, context: ContextTypes.DEFAULT_TYPE, profile: UserProfile
    ) -> None:
        if not self._join_notification_ids:
            return
        username = f"@{profile.username}" if profile.username else "No username"
        text = (
            "🆕 BUILDWISE STUDENT ONBOARDING\n\n"
            f"Name: {profile.student_name or profile.display_name}\n"
            f"Institution: {profile.university or 'Not supplied'}\n"
            f"Telegram: {username}\n"
            f"Telegram ID: {profile.telegram_id}"
        )
        for admin_id in self._join_notification_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except TelegramError:
                logger.warning(
                    "Could not send onboarding notification admin_telegram_id=%s",
                    admin_id,
                )

    async def _offer_timezone_setup(
        self, update: Update, profile: UserProfile
    ) -> None:
        if not profile.timezone_confirmed and not profile.timezone_prompted:
            message = update.effective_message
            if message is not None:
                await message.reply_text(
                    "🌍 One quick setup step: send /timezone followed by your area, for "
                    "example /timezone Africa/Lagos. This keeps streaks, reminders, and "
                    "AI-limit reset times on your local day."
                )
            await self._database.mark_timezone_prompted(profile.id)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        message = update.effective_message
        text = (message.text or message.caption or "") if message is not None else ""
        if not profile.onboarding_completed:
            if message is None or message.photo or message.document:
                await self._begin_onboarding(update, context)
                return
            await self._handle_onboarding_message(update, context, profile, text)
            return

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
    def _usage_limit_message(profile: UserProfile, limit: int) -> str:
        zone = ZoneInfo(profile.timezone or "UTC")
        now = datetime.now(zone)
        reset = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        reset_label = reset.strftime("%A, %d %B %Y at %I:%M %p")
        timezone_note = ""
        if not profile.timezone_confirmed:
            timezone_note = (
                "\n\nYour timezone has not been confirmed yet. Use /timezone Area/City "
                "so BuildWise can show this in your exact local time."
            )
        return (
            "🚦 Daily AI limit reached\n\n"
            f"You have used today's {limit} BuildWise AI requests.\n\n"
            f"⏰ Reset: {reset_label} ({profile.timezone or 'UTC'})\n"
            "Your daily AI requests become available again exactly at that time."
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
                await message.reply_text(
                    f"Please wait {wait} second before sending another AI request."
                )
                return None
            usage = await self._database.consume_usage(
                profile, self._settings.daily_ai_request_limit
            )
            if not usage.allowed:
                await message.reply_text(self._usage_limit_message(profile, usage.limit))
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

    async def _show_home(self, update: Update, profile: UserProfile) -> None:
        stats = await self._database.get_dashboard(profile)
        streak_label = "day" if stats.current_streak == 1 else "days"
        user = update.effective_user
        user_id = user.id if user is not None else None
        greeting_name = profile.student_name or profile.display_name
        text = (
            f"🏠 BuildWise · {greeting_name}\n"
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
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
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
