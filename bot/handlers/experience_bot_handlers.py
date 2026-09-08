"""Student onboarding, tiered limits, copy actions, and operational activity."""

from __future__ import annotations

import logging
from time import monotonic

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot.domain import ConversationTurn, Mode, RequestPayload, UserProfile
from bot.formatting import format_response, split_for_telegram
from bot.handlers.navigation_bot_handlers import NavigationBotHandlers
from bot.keyboards import follow_up_keyboard, practice_count_keyboard
from bot.services.openai_service import AIServiceError
from bot.services.supabase_service import SupabaseError
from bot.student_access import resolve_student_access, usage_limit_message

logger = logging.getLogger(__name__)


class ExperienceBotHandlers(NavigationBotHandlers):
    """Layer new student/admin UX over the existing isolated BuildWise workflows."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._admin_notify_ids = self._settings.admin_notify_telegram_ids

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
        await super().home_command(update, context)

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
            update,
            context,
            mode,
            activity_type=activity_type,
        )

    async def practice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        profile = await self._profile(update)
        if not profile.onboarding_completed:
            await self._begin_onboarding(update, context)
            return
        await super().practice(update, context)

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        profile = await self._profile(update)
        message = update.effective_message
        if message is None:
            return
        raw_text = (message.text or message.caption or "").strip()

        if not profile.onboarding_completed:
            if context.user_data.get("awaiting_onboarding_name"):
                await self._capture_onboarding_name(update, context, raw_text)
                return
            if context.user_data.get("awaiting_onboarding_university"):
                await self._capture_onboarding_university(
                    update, context, profile, raw_text
                )
                return
            await self._begin_onboarding(update, context)
            return

        await super().handle_message(update, context)

    async def _begin_onboarding(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        context.user_data.pop("awaiting_onboarding_university", None)
        context.user_data.pop("onboarding_name", None)
        context.user_data["awaiting_onboarding_name"] = True
        await update.effective_message.reply_text(
            "👋 Welcome to BuildWise AI.\n\n"
            "Before we start, I need two quick details so your learning profile is useful.\n\n"
            "What is your name?"
        )

    async def _capture_onboarding_name(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raw_text: str,
    ) -> None:
        name = " ".join(raw_text.split())
        if not 2 <= len(name) <= 100:
            await update.effective_message.reply_text(
                "Please send the name you want BuildWise to use (2–100 characters)."
            )
            return
        context.user_data.pop("awaiting_onboarding_name", None)
        context.user_data["onboarding_name"] = name
        context.user_data["awaiting_onboarding_university"] = True
        await update.effective_message.reply_text(
            f"Nice to meet you, {name}. 👋\n\n"
            "What university, polytechnic, college, or other institution do you attend?"
        )

    async def _capture_onboarding_university(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        raw_text: str,
    ) -> None:
        university = " ".join(raw_text.split())
        if not 2 <= len(university) <= 160:
            await update.effective_message.reply_text(
                "Please send your institution name (2–160 characters)."
            )
            return
        name = str(context.user_data.get("onboarding_name", "")).strip()
        if not name:
            await self._begin_onboarding(update, context)
            return

        completed = await self._database.complete_onboarding(
            profile.id,
            student_name=name,
            university=university,
        )
        context.user_data.pop("awaiting_onboarding_university", None)
        context.user_data.pop("onboarding_name", None)
        await self._record_event(
            completed,
            event_type="joined",
            detail=f"Completed onboarding · {university}",
        )
        await self._notify_new_user(update, context, completed)
        await update.effective_message.reply_text(
            f"✅ You’re set, {completed.student_name or completed.display_name}.\n\n"
            "BuildWise is ready for your construction questions, calculations, PDFs, quizzes, sketches and images."
        )
        await self._show_home(update, completed)
        if not completed.timezone_confirmed and not completed.timezone_prompted:
            await update.effective_message.reply_text(
                "🌍 One last optional setup: send /timezone followed by your area, for example "
                "/timezone Africa/Lagos. This keeps limits, streaks and reminders on your local day."
            )
            await self._database.mark_timezone_prompted(completed.id)

    async def _notify_new_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
    ) -> None:
        if not self._admin_notify_ids:
            return
        user = update.effective_user
        username = f"@{user.username}" if user is not None and user.username else "—"
        telegram_name = user.full_name if user is not None and user.full_name else profile.display_name
        text = (
            "🆕 New BuildWise student\n\n"
            f"Name: {profile.student_name or profile.display_name}\n"
            f"Institution: {profile.university or '—'}\n"
            f"Telegram name: {telegram_name}\n"
            f"Username: {username}\n"
            f"Telegram ID: {profile.telegram_id}"
        )
        for admin_id in self._admin_notify_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except TelegramError:
                logger.exception(
                    "Could not send new-user admin notification admin_id=%s", admin_id
                )

    async def _record_event(
        self,
        profile: UserProfile,
        *,
        event_type: str,
        mode: str | None = None,
        detail: str | None = None,
    ) -> None:
        try:
            await self._database.record_activity_event(
                user_id=profile.id,
                event_type=event_type,
                mode=mode,
                detail=detail,
            )
        except SupabaseError:
            # Operations analytics must never turn a successful student action into a failure.
            logger.exception("Could not record BuildWise user activity event")

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
        lock = self._user_locks.setdefault(user.id, __import__("asyncio").Lock())
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

            access = resolve_student_access(self._settings, profile.telegram_id)
            usage = await self._database.consume_usage(profile, access.daily_limit)
            if not usage.allowed:
                await message.reply_text(usage_limit_message(profile, usage))
                await self._record_event(
                    profile,
                    event_type="limit_reached",
                    mode=mode.value,
                    detail=f"{access.tier.value} · {usage.limit} requests",
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
                await self._record_event(
                    profile,
                    event_type="ai_request_failed",
                    mode=mode.value,
                    detail=exc.public_message,
                )
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

            input_kind = "PDF" if payload.has_pdf else "image" if payload.has_image else "text"
            await self._record_event(
                profile,
                event_type="ai_request",
                mode=mode.value,
                detail=f"{input_kind} · {access.tier.value}",
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

    async def _handle_followup(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        profile: UserProfile,
        action: str,
    ) -> None:
        if action == "copy":
            last = await self._last_context(context, profile)
            if not last:
                await update.effective_message.reply_text("Ask a question first.")
                return
            mode, _, answer = last
            copy_text = format_response(mode, answer)
            for chunk in split_for_telegram(copy_text):
                await update.effective_message.reply_text(chunk)
            return
        await super()._handle_followup(update, context, profile, action)

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
        access = resolve_student_access(self._settings, profile.telegram_id)
        usage = await self._database.consume_usage(profile, access.daily_limit)
        if not usage.allowed:
            context.user_data.pop("practice_generating", None)
            await update.effective_message.reply_text(usage_limit_message(profile, usage))
            await self._record_event(
                profile,
                event_type="limit_reached",
                mode=Mode.STUDY.value,
                detail=f"practice · {access.tier.value} · {usage.limit} requests",
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
        await self._record_event(
            profile,
            event_type="practice_generated",
            mode=Mode.STUDY.value,
            detail=f"{count} questions · {setup['difficulty']} · {access.tier.value}",
        )
        await loading.edit_text("Practice ready.")
        await self._send_practice_question(update, context)
