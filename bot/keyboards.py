"""Inline keyboards for BuildWise's menu and short workflows."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain import Mode, UserProfile


def home_keyboard(*, timezone_confirmed: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📚 Study & Solve", callback_data="home:study")],
        [
            InlineKeyboardButton("📐 Analyze Sketch", callback_data="home:sketch"),
            InlineKeyboardButton("🏗️ Analyze Building", callback_data="home:building"),
        ],
        [InlineKeyboardButton("🧠 Quick Practice", callback_data="home:practice")],
        [InlineKeyboardButton("▶️ Continue Studying", callback_data="home:continue")],
        [
            InlineKeyboardButton("📂 History", callback_data="home:history"),
            InlineKeyboardButton("⚙️ Settings", callback_data="home:settings"),
        ],
    ]
    if not timezone_confirmed:
        rows.append([InlineKeyboardButton("🌍 Set timezone", callback_data="settings:timezone")])
    return InlineKeyboardMarkup(rows)


def follow_up_keyboard(mode: Mode) -> InlineKeyboardMarkup:
    """Keep the primary answer surface to two actions at most."""

    if mode is Mode.STUDY:
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("📋 Copy", callback_data="followup:copy"),
                InlineKeyboardButton("📄 PDF", callback_data="followup:pdf"),
            ]]
        )
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📋 Copy", callback_data="followup:copy"),
            InlineKeyboardButton("🔄 Analyze Another", callback_data="followup:new"),
        ]]
    )


def sketch_analysis_keyboard(report_key: str, *, can_generate_elevation: bool) -> InlineKeyboardMarkup:
    """Two-action sketch surface: front elevation when possible, plus PDF."""

    if can_generate_elevation:
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🏠 Elevation", callback_data=f"elevgen:{report_key}"),
                InlineKeyboardButton("📄 PDF", callback_data=f"sketchpdf:{report_key}"),
            ]]
        )
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📋 Copy", callback_data="followup:copy"),
            InlineKeyboardButton("📄 PDF", callback_data=f"sketchpdf:{report_key}"),
        ]]
    )


def elevation_result_keyboard(report_key: str) -> InlineKeyboardMarkup:
    """Front elevation result: keep only the next advanced step and PDF."""

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🧱 More Views", callback_data=f"elevmenu:{report_key}"),
            InlineKeyboardButton("📄 Add to PDF", callback_data=f"sketchpdf:{report_key}"),
        ]]
    )


def more_views_keyboard(report_key: str) -> InlineKeyboardMarkup:
    """Advanced sketch submenu; complexity is hidden until the student asks for it."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ Left Side", callback_data=f"elevview:left:{report_key}"),
                InlineKeyboardButton("➡️ Right Side", callback_data=f"elevview:right:{report_key}"),
            ],
            [
                InlineKeyboardButton("🔙 Rear", callback_data=f"elevview:rear:{report_key}"),
                InlineKeyboardButton("🔝 Top / Roof", callback_data=f"elevview:top:{report_key}"),
            ],
            [InlineKeyboardButton("↩️ Back", callback_data=f"elevback:{report_key}")],
        ]
    )


def view_result_keyboard(report_key: str, view: str) -> InlineKeyboardMarkup:
    """Generated side/rear/top view actions remain at two buttons."""

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📄 Add to PDF", callback_data=f"sketchpdf:{report_key}"),
            InlineKeyboardButton("🔁 Regenerate", callback_data=f"elevregen:{view}:{report_key}"),
        ]]
    )


def clarification_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Solve It", callback_data="route:study"),
                InlineKeyboardButton("Explain It", callback_data="route:explain"),
            ],
            [
                InlineKeyboardButton("Analyze Building", callback_data="route:building"),
                InlineKeyboardButton("Analyze Sketch", callback_data="route:sketch"),
            ],
        ]
    )


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 Study & Solve", callback_data="mode:study")],
            [
                InlineKeyboardButton("📐 Sketch", callback_data="mode:sketch"),
                InlineKeyboardButton("🏗️ Building", callback_data="mode:building"),
            ],
            [InlineKeyboardButton("✨ Automatic", callback_data="mode:auto")],
        ]
    )


def settings_keyboard(profile: UserProfile) -> InlineKeyboardMarkup:
    style = "Detailed" if profile.answer_style.value == "detailed" else "Concise"
    buttons = "On" if profile.follow_up_buttons else "Off"
    history = "On" if profile.save_history else "Off"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Answer style: {style}", callback_data="settings:style")],
            [InlineKeyboardButton(f"Follow-up buttons: {buttons}", callback_data="settings:buttons")],
            [InlineKeyboardButton(f"Save history: {history}", callback_data="settings:history")],
            [
                InlineKeyboardButton("🌍 Timezone", callback_data="settings:timezone"),
                InlineKeyboardButton("🎯 Daily goal", callback_data="settings:goal"),
            ],
            [InlineKeyboardButton("🔔 Reminders", callback_data="settings:reminders")],
            [InlineKeyboardButton("🏠 Home", callback_data="home:menu")],
        ]
    )


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1", callback_data="goal:1"),
                InlineKeyboardButton("3", callback_data="goal:3"),
                InlineKeyboardButton("5", callback_data="goal:5"),
                InlineKeyboardButton("10", callback_data="goal:10"),
            ],
            [InlineKeyboardButton("Custom", callback_data="goal:custom")],
        ]
    )


def reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Morning", callback_data="reminder:08:00"),
                InlineKeyboardButton("Afternoon", callback_data="reminder:14:00"),
                InlineKeyboardButton("Evening", callback_data="reminder:19:00"),
            ],
            [
                InlineKeyboardButton("Custom time", callback_data="reminder:custom"),
                InlineKeyboardButton("Off", callback_data="reminder:off"),
            ],
        ]
    )


def reminder_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Start Practice", callback_data="home:practice")],
            [InlineKeyboardButton("Remind Me Later", callback_data="reminder:snooze")],
        ]
    )


def practice_difficulty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Easy", callback_data="practice:difficulty:easy"),
            InlineKeyboardButton("Medium", callback_data="practice:difficulty:medium"),
            InlineKeyboardButton("Hard", callback_data="practice:difficulty:hard"),
        ]]
    )


def practice_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("3 Questions", callback_data="practice:count:3"),
            InlineKeyboardButton("5 Questions", callback_data="practice:count:5"),
            InlineKeyboardButton("10 Questions", callback_data="practice:count:10"),
        ]]
    )


def quiz_answer_keyboard() -> InlineKeyboardMarkup:
    labels = ("A", "B", "C", "D")
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(label, callback_data=f"practice:answer:{index}")
            for index, label in enumerate(labels)
        ]]
    )


def quiz_next_keyboard(*, finished: bool = False) -> InlineKeyboardMarkup:
    label = "See Results" if finished else "Next Question"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data="practice:next")]]
    )


def history_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Delete all history", callback_data="history:delete")]]
    )


def history_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Yes, delete", callback_data="history:confirm_delete"),
            InlineKeyboardButton("Cancel", callback_data="history:cancel_delete"),
        ]]
    )
