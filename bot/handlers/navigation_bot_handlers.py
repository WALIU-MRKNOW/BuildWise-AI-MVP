"""Role-aware Telegram navigation layered on the existing student handlers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, time, timedelta, timezone
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
        await super().start(update, context)

    async def home_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
        await self._show_home(update, profile)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._handle_onboarding_message(update, context, profile)
            return

        # Remove any stale onboarding state after a completed profile is loaded.
        context.user_data.pop("onboarding_step", None)
        context.user_data.pop("onboarding_name", None)

        message = update.effective_message
        text = (message.text or message.caption or "") if message is not None else ""
        token = _current_study_request_kind.set(detect_study_request_kind(text))
        try:
            await super().handle_message(update, context)
        finally:
            _current_study_request_kind.reset(token)

    async def _mode_command_with_optional_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        mode: Mode,
        *,
        activity_type: str | None = None,
    ) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
        await super()._mode_command_with_optional_text(
            update, context, mode, activity_type=activity_type
        )

    async def practice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
        await super().practice(update, context)

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

    async def _begin_onboarding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        context.user_data["onboarding_step"] = "name"
        context.user_data.pop("onboarding_name", None)
        await update.effective_message.reply_text(
            "👋 Welcome to BuildWise AI.\n\n"
            "Before we begin, I need two quick details so your study experience is properly set up.\n\n"
            "What is your name?"
        )

    async def _handle_onboarding_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        text = (message.text or "").strip()
        step = context.user_data.get("onboarding_step")

        if step not in {"name", "university"}:
            await self._begin_onboarding(update, context)
            return
        if not text:
            await message.reply_text("Please send your answer as text.")
            return

        if step == "name":
            cleaned = " ".join(text.split())
            if not 2 <= len(cleaned) <= 80:
                await message.reply_text("Please send your name using 2 to 80 characters.")
                return
            context.user_data["onboarding_name"] = cleaned
            context.user_data["onboarding_step"] = "university"
            await message.reply_text(
                f"Nice to meet you, {cleaned}. 👋\n\n"
                "Which university, polytechnic, college, or institution do you attend?"
            )
            return

        name = str(context.user_data.get("onboarding_name") or "").strip()
        if not name:
            context.user_data["onboarding_step"] = "name"
            await message.reply_text("What is your name?")
            return
        university = " ".join(text.split())
        if not 2 <= len(university) <= 160:
            await message.reply_text(
                "Please send the institution name using 2 to 160 characters."
            )
            return

        try:
            completed = await self._database.complete_onboarding(
                profile, display_name=name, university=university
            )
        except (ValueError, SupabaseError) as exc:
            await message.reply_text(str(exc))
            return

        context.user_data.pop("onboarding_step", None)
        context.user_data.pop("onboarding_name", None)
        await self._notify_new_student(context, completed)
        await message.reply_text(
            f"✅ You're set, {completed.display_name}.\n\n"
            f"Institution: {completed.university}\n"
            "Welcome to BuildWise AI. Let's study. 🏗️📚"
        )
        await self._show_home(update, completed)
        if not completed.timezone_confirmed and not completed.timezone_prompted:
            await message.reply_text(
                "🌍 One last setup step: send /timezone followed by your area, for "
                "example /timezone Africa/Lagos. This keeps limits, streaks and reminders on your local day."
            )
            await self._database.mark_timezone_prompted(completed.id)

    async def _notify_new_student(
        self, context: ContextTypes.DEFAULT_TYPE, profile: UserProfile
    ) -> None:
        username = f"@{profile.username}" if profile.username else "—"
        text = (
            "🆕 New BuildWise student\n\n"
            f"Name: {profile.display_name}\n"
            f"Institution: {profile.university or '—'}\n"
            f"Username: {username}\n"
            f"Telegram ID: {profile.telegram_id}"
        )
        for admin_id in self._settings.buildwise_admin_telegram_ids:
            if admin_id == profile.telegram_id:
                continue
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except TelegramError:
                # Admin notifications must never block a student's onboarding.
                pass

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
    def _reset_label(profile: UserProfile) -> str:
        try:
            tz = ZoneInfo(profile.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
        now = datetime.now(timezone.utc).astimezone(tz)
        next_day = now.date() + timedelta(days=1)
        reset = datetime.combine(next_day, time.min, tzinfo=tz)
        hour = reset.strftime("%I:%M %p").lstrip("0")
        date_label = reset.strftime("%A, %d %B %Y").replace(" 0", " ")
        return f"{hour} on {date_label} ({profile.timezone})"

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
                await message.reply_text(
                    "🚦 Daily AI limit reached\n\n"
                    f"You've used all {usage.limit} BuildWise AI requests available for today.\n\n"
                    f"Your allowance resets at exactly {self._reset_label(profile)}.\n\n"
                    "After that time, you can continue with a fresh daily allowance."
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
                        pass
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
                    if payload.file_reference:
                        try:
                            await self._database.delete_file(payload.file_reference)
                            payload.file_reference = None
                        except SupabaseError:
                            pass
            elif payload.file_reference:
                try:
                    await self._database.delete_file(payload.file_reference)
                    payload.file_reference = None
                except SupabaseError:
                    pass

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
                    pass
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
            await update.effective_message.reply_text(
                "Your practice is already being prepared."
            )
            return
        context.user_data["practice_generating"] = True
        usage = await self._database.consume_usage(
            profile, self._settings.daily_ai_request_limit
        )
        if not usage.allowed:
            context.user_data.pop("practice_generating", None)
            await update.effective_message.reply_text(
                "🚦 Daily AI limit reached\n\n"
                f"You've used all {usage.limit} BuildWise AI requests available for today.\n\n"
                f"Your allowance resets at exactly {self._reset_label(profile)}."
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

    async def _show_home(self, update: Update, profile: UserProfile) -> None:
        stats = await self._database.get_dashboard(profile)
        streak_label = "day" if stats.current_streak == 1 else "days"
        user = update.effective_user
        user_id = user.id if user is not None else None
        text = (
            "🏠 BuildWise\n"
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
