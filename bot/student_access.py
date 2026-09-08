"""Student access policy helpers kept separate from BuildWise Pro limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.config import Settings
from bot.domain import UserProfile, UsageStatus


class StudentAccessTier(StrEnum):
    NORMAL = "normal"
    PILOT = "pilot"
    FOUNDER = "founder"


@dataclass(frozen=True, slots=True)
class StudentAccessPolicy:
    tier: StudentAccessTier
    daily_limit: int
    pilot_expires_at: datetime | None = None


def resolve_student_access(
    settings: Settings,
    telegram_id: int,
    *,
    now: datetime | None = None,
) -> StudentAccessPolicy:
    """Resolve only the public student AI allowance; Pro remains independent."""

    if telegram_id in settings.founder_telegram_ids:
        return StudentAccessPolicy(
            StudentAccessTier.FOUNDER,
            settings.founder_daily_ai_request_limit,
        )

    current = now or datetime.now(timezone.utc)
    pilot_until = settings.pilot_access_until
    if (
        telegram_id in settings.pilot_telegram_ids
        and pilot_until is not None
        and current < pilot_until
    ):
        return StudentAccessPolicy(
            StudentAccessTier.PILOT,
            settings.pilot_daily_ai_request_limit,
            pilot_until,
        )

    return StudentAccessPolicy(
        StudentAccessTier.NORMAL,
        settings.daily_ai_request_limit,
    )


def next_usage_reset(profile: UserProfile, *, now: datetime | None = None) -> datetime:
    """Return the next local midnight in the student's saved timezone."""

    current = now or datetime.now(timezone.utc)
    try:
        zone = ZoneInfo(profile.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    local_now = current.astimezone(zone)
    reset_date = local_now.date() + timedelta(days=1)
    return datetime.combine(reset_date, time.min, tzinfo=zone)


def usage_limit_message(
    profile: UserProfile,
    usage: UsageStatus,
    *,
    now: datetime | None = None,
) -> str:
    """Create a student-friendly limit message with an exact local reset time."""

    reset = next_usage_reset(profile, now=now)
    reset_text = reset.strftime("%A, %d %B at %I:%M %p")
    return (
        f"You’ve reached today’s {usage.limit}-request BuildWise AI limit.\n\n"
        f"⏰ Your access resets on {reset_text} ({profile.timezone}).\n"
        "Your saved work, history and study tools are still available until then."
    )
