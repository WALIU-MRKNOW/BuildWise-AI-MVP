"""Core data types shared across BuildWise services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    STUDY = "study"
    SKETCH = "sketch"
    BUILDING = "building"

    @property
    def label(self) -> str:
        return {
            Mode.STUDY: "Study & Solve",
            Mode.SKETCH: "Sketch / Architecture Analysis",
            Mode.BUILDING: "Building Analysis",
        }[self]


class AnswerStyle(StrEnum):
    CONCISE = "concise"
    DETAILED = "detailed"


@dataclass(slots=True)
class RequestPayload:
    text: str = ""
    image_bytes: bytes | None = None
    image_mime_type: str | None = None
    pdf_bytes: bytes | None = None
    filename: str | None = None
    file_reference: str | None = None

    @property
    def has_image(self) -> bool:
        return self.image_bytes is not None

    @property
    def has_pdf(self) -> bool:
        return self.pdf_bytes is not None

    @property
    def has_attachment(self) -> bool:
        return self.has_image or self.has_pdf


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class UserProfile:
    id: str
    telegram_id: int
    username: str | None
    display_name: str
    university: str | None
    onboarding_completed: bool
    timezone: str
    timezone_confirmed: bool
    timezone_prompted: bool
    answer_style: AnswerStyle
    follow_up_buttons: bool
    save_history: bool
    default_daily_goal: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "UserProfile":
        try:
            style = AnswerStyle(row.get("answer_style", "concise"))
        except ValueError:
            style = AnswerStyle.CONCISE
        return cls(
            id=str(row["id"]),
            telegram_id=int(row["telegram_id"]),
            username=row.get("username"),
            display_name=row.get("display_name") or "Student",
            university=(str(row.get("university")).strip() if row.get("university") else None),
            onboarding_completed=bool(row.get("onboarding_completed", True)),
            timezone=row.get("timezone") or "UTC",
            timezone_confirmed=bool(row.get("timezone_confirmed", False)),
            timezone_prompted=bool(row.get("timezone_prompted", False)),
            answer_style=style,
            follow_up_buttons=bool(row.get("follow_up_buttons", True)),
            save_history=bool(row.get("save_history", True)),
            default_daily_goal=int(row.get("default_daily_goal", 5)),
        )


@dataclass(frozen=True, slots=True)
class DashboardStats:
    current_streak: int = 0
    longest_streak: int = 0
    total_study_days: int = 0
    completed_today: int = 0
    daily_target: int = 5
    last_activity_date: date | None = None


@dataclass(frozen=True, slots=True)
class StudyCompletion:
    current_streak: int
    longest_streak: int
    completed_today: int
    daily_target: int
    milestone: int | None = None
    goal_just_completed: bool = False


@dataclass(frozen=True, slots=True)
class UsageStatus:
    allowed: bool
    used: int
    limit: int
    reservation_id: str | None = None
    source: str | None = None
    bonus_credits_remaining: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass(frozen=True, slots=True)
class QuizQuestion:
    question: str
    options: tuple[str, str, str, str]
    correct_index: int
    explanation: str


@dataclass(frozen=True, slots=True)
class DueReminder:
    user_id: str
    telegram_id: int
    local_date: date
    current_streak: int
    daily_target: int
    daily_completed: int
    reminder_type: str


@dataclass(frozen=True, slots=True)
class WeeklyProgress:
    questions_solved: int
    practice_completed: int
    accuracy: float | None
    current_streak: int
    longest_streak: int
    most_studied_topic: str | None
    weakest_topic: str | None
    study_days: int
