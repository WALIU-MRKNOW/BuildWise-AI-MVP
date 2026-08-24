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
            count = await self._service.referral_count(registration.user_id)
        except (ReferralServiceError, ValueError):
            await message.reply_text("Invite links are temporarily unavailable. Please try again later.")
            return
        await message.reply_text(
            "👥 Invite friends to BuildWise\n\n"
            f"Your link:\n{link}\n\n"
            f"Successful referrals: {count}\n\n"
            "Referral tracking is active. Rewards are not enabled yet."
        )

    async def add_bot_command(self, application: Application) -> None:
        commands = list(await application.bot.get_my_commands())
        if not any(command.command == "invite" for command in commands):
            commands.append(BotCommand("invite", "Get your referral link and count"))
            await application.bot.set_my_commands(commands)
