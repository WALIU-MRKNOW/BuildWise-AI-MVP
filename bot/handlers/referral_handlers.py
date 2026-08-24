"""Telegram referral commands kept separate from AI/image/PDF handlers."""

from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.handlers.bot_handlers import BotHandlers
from bot.services.referral_service import (
    ReferralService,
    ReferralServiceError,
    build_referral_link,
)


class ReferralHandlers:
    def __init__(self, service: ReferralService, existing_handlers: BotHandlers) -> None:
        self._service = service
        self._existing = existing_handlers

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start), group=0)
        application.add_handler(CommandHandler("invite", self.invite), group=0)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is not None:
            referral_code = context.args[0] if context.args else None
            try:
                await self._service.register_start(
                    telegram_id=user.id,
                    username=user.username,
                    display_name=user.full_name or "Student",
                    referral_code=referral_code,
                )
            except ReferralServiceError:
                # Referral tracking must never block the normal BuildWise start flow.
                pass
        await self._existing.start(update, context)

    async def invite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        try:
            registration = await self._service.register_start(
                telegram_id=user.id,
                username=user.username,
                display_name=user.full_name or "Student",
                referral_code=None,
            )
            me = await context.bot.get_me()
            if not me.username:
                raise ReferralServiceError("Bot username is unavailable")
            link = build_referral_link(me.username, registration.referral_code)
            rewards = await self._service.reward_summary(registration.user_id)
        except (ReferralServiceError, ValueError):
            await message.reply_text("Invite links are temporarily unavailable. Please try again later.")
            return
        await message.reply_text(
            "👥 Invite friends to BuildWise\n\n"
            f"Your link:\n{link}\n\n"
            f"Successful referrals: {rewards.successful_referrals}\n"
            f"Qualified referrals: {rewards.qualified_referrals}\n"
            f"🎁 Bonus AI requests available: {rewards.available_credits}\n"
            f"Lifetime bonus requests earned: {rewards.lifetime_earned}\n\n"
            "Each referred friend earns you 10 bonus AI requests after their first successful AI request."
        )

    async def add_bot_command(self, application: Application) -> None:
        commands = list(await application.bot.get_my_commands())
        if not any(command.command == "invite" for command in commands):
            commands.append(BotCommand("invite", "Get your referral link and rewards"))
            await application.bot.set_my_commands(commands)
