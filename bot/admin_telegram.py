"""Private BuildWise Telegram admin bot and operator commands."""

from __future__ import annotations

from datetime import datetime
import logging

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.config import Settings
from bot.pro.beta_config import validated_admin_telegram_ids
from bot.services.admin_service import AdminService, AdminServiceError

logger = logging.getLogger(__name__)

ADMIN_HELP = """🛡 BUILDWISE ADMIN

/users — recent students, Telegram IDs and tiers
/activity — recent BuildWise student activity
/lookup <telegram_id> — inspect one student
/pilot <telegram_id> [days] — temporary pilot limit (default 14 days)
/normal <telegram_id> — return a student to the normal limit
/stats — operational summary
/myid — show your Telegram ID
/help — show this guide"""


def _short(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _date(value: object) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:16]
    return parsed.strftime("%d %b %Y, %H:%M UTC")


class AdminTelegramHandlers:
    """Read-only operations plus explicit pilot-tier changes for authorized admins."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = AdminService(settings)
        self._admin_ids = validated_admin_telegram_ids()

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.help_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("users", self.users_command))
        application.add_handler(CommandHandler("activity", self.activity_command))
        application.add_handler(CommandHandler("lookup", self.lookup_command))
        application.add_handler(CommandHandler("pilot", self.pilot_command))
        application.add_handler(CommandHandler("normal", self.normal_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(CommandHandler("myid", self.myid_command))

    async def post_init(self, application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("users", "List recent students and Telegram IDs"),
                BotCommand("activity", "Show recent BuildWise activity"),
                BotCommand("lookup", "Look up one Telegram ID"),
                BotCommand("pilot", "Give a student temporary pilot access"),
                BotCommand("normal", "Return a student to normal limits"),
                BotCommand("stats", "Show BuildWise operational stats"),
                BotCommand("myid", "Show your Telegram ID"),
                BotCommand("help", "Show admin commands"),
            ]
        )

    async def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is not None and user.id in self._admin_ids:
            return True
        if update.effective_message is not None:
            await update.effective_message.reply_text("Admin access required.")
        return False

    async def help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(ADMIN_HELP)

    async def myid_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user = update.effective_user
        if user is None:
            return
        if user.id not in self._admin_ids:
            await update.effective_message.reply_text(f"Your Telegram ID: {user.id}")
            return
        await update.effective_message.reply_text(f"Admin Telegram ID: {user.id}")

    async def users_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        try:
            students = await self._service.list_students(limit=20)
        except AdminServiceError:
            logger.exception("Admin Telegram /users failed")
            await update.effective_message.reply_text("Admin data is temporarily unavailable.")
            return
        if not students:
            await update.effective_message.reply_text("No BuildWise students yet.")
            return
        lines = ["👥 BUILDWISE STUDENTS"]
        for student in students:
            username = f"@{student['username']}" if student.get("username") else "No username"
            tier = str(student.get("tier") or "Normal")
            if student.get("pilot_until"):
                tier += f" until {str(student['pilot_until'])[:10]}"
            lines.append(
                "\n"
                f"{_short(student.get('name') or 'Student', 60)}\n"
                f"🏫 {_short(student.get('university') or 'Institution not added', 90)}\n"
                f"Telegram: {username}\n"
                f"ID: {student['telegram_id']}\n"
                f"Tier: {tier}\n"
                f"Joined: {_date(student.get('created_at'))}"
            )
        await update.effective_message.reply_text("\n".join(lines))

    async def activity_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        try:
            rows = await self._service.recent_admin_activity(limit=15)
        except AdminServiceError:
            logger.exception("Admin Telegram /activity failed")
            await update.effective_message.reply_text("Recent activity is temporarily unavailable.")
            return
        if not rows:
            await update.effective_message.reply_text("No recent BuildWise activity.")
            return
        lines = ["🕒 RECENT BUILDWISE ACTIVITY"]
        for row in rows:
            username = f"@{row['username']}" if row.get("username") else "No username"
            lines.append(
                "\n"
                f"{_date(row.get('created_at'))}\n"
                f"{_short(row.get('name') or 'Student', 50)} · {username} · ID {row.get('telegram_id') or '—'}\n"
                f"{_short(row.get('university') or 'Institution not added', 80)} · {row.get('tier') or 'Normal'}\n"
                f"Mode: {row.get('mode') or 'unknown'}\n"
                f"Prompt: {_short(row.get('message'), 180)}"
            )
        await update.effective_message.reply_text("\n".join(lines))

    async def lookup_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        telegram_id = self._telegram_id_arg(context)
        if telegram_id is None:
            await update.effective_message.reply_text("Use /lookup <telegram_id>.")
            return
        try:
            student = await self._service.student_by_telegram_id(telegram_id)
        except AdminServiceError:
            logger.exception("Admin Telegram /lookup failed")
            await update.effective_message.reply_text("Admin data is temporarily unavailable.")
            return
        if student is None:
            await update.effective_message.reply_text("That Telegram ID is not in BuildWise.")
            return
        username = f"@{student['username']}" if student.get("username") else "No username"
        tier = str(student.get("tier") or "Normal")
        if student.get("pilot_until"):
            tier += f" until {_date(student['pilot_until'])}"
        await update.effective_message.reply_text(
            "👤 STUDENT\n\n"
            f"Name: {student.get('name') or 'Student'}\n"
            f"Institution: {student.get('university') or 'Institution not added'}\n"
            f"Telegram: {username}\n"
            f"Telegram ID: {student['telegram_id']}\n"
            f"Tier: {tier}\n"
            f"Joined: {_date(student.get('created_at'))}\n"
            f"Last profile update: {_date(student.get('updated_at'))}"
        )

    async def pilot_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        telegram_id = self._telegram_id_arg(context)
        if telegram_id is None:
            await update.effective_message.reply_text("Use /pilot <telegram_id> [days].")
            return
        days = 14
        if len(context.args) >= 2:
            try:
                days = int(context.args[1])
            except ValueError:
                await update.effective_message.reply_text("Pilot days must be a whole number from 1 to 90.")
                return
        try:
            expires_at = await self._service.set_pilot(telegram_id, days=days)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        except AdminServiceError:
            logger.exception("Admin Telegram /pilot failed")
            await update.effective_message.reply_text("Could not update the student tier right now.")
            return
        await update.effective_message.reply_text(
            f"✅ Telegram ID {telegram_id} is now a Pilot student for {days} day(s).\n"
            f"Expires: {_date(expires_at.isoformat())}"
        )

    async def normal_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        telegram_id = self._telegram_id_arg(context)
        if telegram_id is None:
            await update.effective_message.reply_text("Use /normal <telegram_id>.")
            return
        try:
            await self._service.set_normal(telegram_id)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return
        except AdminServiceError:
            logger.exception("Admin Telegram /normal failed")
            await update.effective_message.reply_text("Could not update the student tier right now.")
            return
        await update.effective_message.reply_text(
            f"✅ Telegram ID {telegram_id} now uses the normal student limit."
        )

    async def stats_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        try:
            data = await self._service.dashboard()
        except AdminServiceError:
            logger.exception("Admin Telegram /stats failed")
            await update.effective_message.reply_text("Admin stats are temporarily unavailable.")
            return
        summary = data["summary"]
        await update.effective_message.reply_text(
            "📊 BUILDWISE STATS\n\n"
            f"Users: {summary['users']}\n"
            f"Active · 7d: {summary['active_users_7d']}\n"
            f"AI requests · today: {summary['requests_today']}\n"
            f"Conversations · 30d: {summary['conversations_30d']}\n"
            f"Open support tickets: {summary['open_tickets']}"
        )

    @staticmethod
    def _telegram_id_arg(context: ContextTypes.DEFAULT_TYPE) -> int | None:
        if not context.args:
            return None
        try:
            value = int(context.args[0])
        except ValueError:
            return None
        return value if value > 0 else None


def build_admin_telegram_application(settings: Settings) -> Application | None:
    """Build the private admin bot only when a separate token is configured."""

    token_setting = settings.admin_telegram_bot_token
    if token_setting is None:
        return None
    token = token_setting.get_secret_value().strip()
    if not token:
        return None
    application = Application.builder().token(token).build()
    AdminTelegramHandlers(settings).register(application)
    return application
