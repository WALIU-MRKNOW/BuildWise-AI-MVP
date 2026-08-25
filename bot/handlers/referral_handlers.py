"""Telegram referral commands kept separate from AI/image/PDF handlers."""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.handlers.bot_handlers import BotHandlers
from bot.services.referral_service import (
    ReferralService,
    ReferralServiceError,
    build_referral_link,
    normalize_referral_code,
)

logger = logging.getLogger(__name__)


def referral_start_payload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[str | None, bool]:
    """Return a normalized Telegram /start payload and whether one was supplied.

    python-telegram-bot normally exposes deep-link payloads via ``context.args``.
    The raw-message fallback keeps referral attribution correct if command parsing
    differs across webhook/update shapes.
    """
    raw: str | None = None
    if context.args:
        raw = context.args[0]
    else:
        message = update.effective_message
        text = message.text.strip() if message is not None and message.text else ""
        if text:
            command, separator, remainder = text.partition(" ")
            if separator and command.split("@", 1)[0].lower() == "/start":
                raw = remainder.strip().split(maxsplit=1)[0] if remainder.strip() else None
    supplied = bool(raw)
    return normalize_referral_code(raw), supplied


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
            referral_code, payload_supplied = referral_start_payload(update, context)
            logger.info(
                "Referral start telegram_id=%s payload_supplied=%s payload_valid=%s",
                user.id,
                payload_supplied,
                referral_code is not None,
            )
            try:
                registration = await self._service.register_start(
                    telegram_id=user.id,
                    username=user.username,
                    display_name=user.full_name or "Student",
                    referral_code=referral_code,
                )
                logger.info(
                    "Referral registration telegram_id=%s was_new=%s attributed=%s has_referrer=%s",
                    user.id,
                    registration.was_new,
                    registration.attributed,
                    registration.referred_by is not None,
                )
            except ReferralServiceError:
                # Referral tracking must never block the normal BuildWise start flow.
                logger.exception("Referral registration failed telegram_id=%s", user.id)
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
