"""Role-aware Telegram navigation layered on the existing student handlers."""

from __future__ import annotations

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.domain import UserProfile
from bot.handlers.bot_handlers import BotHandlers
from bot.pro.beta_config import validated_admin_telegram_ids, validated_pro_allowlist


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
            InlineKeyboardButton("📝 Quick Practice", callback_data="home:practice"),
            InlineKeyboardButton("📄 Analyze PDF", callback_data="home:pdf"),
        ],
        [
            InlineKeyboardButton("🖼 Image / Sketch", callback_data="home:sketch"),
            InlineKeyboardButton("🏗 Building Photo", callback_data="home:building"),
        ],
        [InlineKeyboardButton("🎯 Study Tools", callback_data="home:studytools")],
        [
            InlineKeyboardButton("🛟 Support", callback_data="nav:support"),
            InlineKeyboardButton("🆔 My Telegram ID", callback_data="nav:myid"),
        ],
    ]
    if pro_access:
        rows.append(
            [
                InlineKeyboardButton("🧰 BuildWise Pro", callback_data="nav:pro"),
                InlineKeyboardButton("📊 Pro Status", callback_data="nav:pro_status"),
            ]
        )
    if admin_access:
        rows.append(
            [InlineKeyboardButton("📈 Pro Beta Stats", callback_data="nav:pro_beta_stats")]
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
            BotCommand("menu", "Open the BuildWise button menu"),
            BotCommand("myid", "Show your Telegram numeric ID"),
        ]
        existing = {command.command for command in commands}
        for command in additions:
            if command.command not in existing:
                commands.append(command)
        await application.bot.set_my_commands(commands)

    async def _show_home(self, update: Update, profile: UserProfile) -> None:
        stats = await self._database.get_dashboard(profile)
        streak = f"🔥 Streak: {stats.current_streak} Day" + (
            "" if stats.current_streak == 1 else "s"
        )
        user = update.effective_user
        user_id = user.id if user is not None else None
        text = (
            "🏠 BUILDWISE AI\n"
            "Plan • Learn • Analyze • Build Better\n\n"
            "What can I help you with?\n\n"
            f"{streak}\n"
            f"🎯 Today: {stats.completed_today} / {stats.daily_target}\n\n"
            "Choose an option below, or send a message, image, PDF, or sketch directly."
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
                "📄 Send the PDF you want BuildWise to analyze. You can add a question or instruction with it."
            )
            return
        if action == "studytools":
            await update.effective_message.reply_text(
                "🎯 STUDY TOOLS\n\nChoose what you want to manage:",
                reply_markup=study_tools_keyboard(),
            )
            return
        if action == "progress":
            await self.progress_command(update, context)
            return
        await super()._handle_home_callback(update, context, profile, action)
