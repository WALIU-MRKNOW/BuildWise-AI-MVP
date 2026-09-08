from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from bot.domain import AnswerStyle, Mode, UserProfile, UsageStatus
from bot.keyboards import follow_up_keyboard
from bot.student_access import (
    StudentAccessTier,
    next_usage_reset,
    resolve_student_access,
    usage_limit_message,
)


class StudentAccessTests(unittest.TestCase):
    def settings(self, *, pilot_until: datetime | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            founder_telegram_ids=frozenset({6901754599}),
            founder_daily_ai_request_limit=1000,
            pilot_telegram_ids=frozenset({222}),
            pilot_daily_ai_request_limit=50,
            pilot_access_until=pilot_until,
            daily_ai_request_limit=20,
        )

    def profile(self) -> UserProfile:
        return UserProfile(
            id="00000000-0000-0000-0000-000000000001",
            telegram_id=333,
            username="student",
            display_name="Student",
            timezone="Africa/Lagos",
            timezone_confirmed=True,
            timezone_prompted=True,
            answer_style=AnswerStyle.CONCISE,
            follow_up_buttons=True,
            save_history=True,
            default_daily_goal=5,
        )

    def test_founder_gets_high_testing_allowance(self) -> None:
        policy = resolve_student_access(self.settings(), 6901754599)
        self.assertEqual(policy.tier, StudentAccessTier.FOUNDER)
        self.assertEqual(policy.daily_limit, 1000)

    def test_pilot_is_temporary_and_normal_limit_returns_after_expiry(self) -> None:
        before = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
        expiry = datetime(2026, 9, 15, 0, tzinfo=timezone.utc)
        active = resolve_student_access(
            self.settings(pilot_until=expiry), 222, now=before
        )
        expired = resolve_student_access(
            self.settings(pilot_until=expiry),
            222,
            now=datetime(2026, 9, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(active.tier, StudentAccessTier.PILOT)
        self.assertEqual(active.daily_limit, 50)
        self.assertEqual(expired.tier, StudentAccessTier.NORMAL)
        self.assertEqual(expired.daily_limit, 20)

    def test_limit_message_shows_exact_next_local_midnight(self) -> None:
        profile = self.profile()
        now = datetime(2026, 9, 8, 22, 30, tzinfo=timezone.utc)
        reset = next_usage_reset(profile, now=now)
        self.assertEqual(reset.isoformat(), "2026-09-09T00:00:00+01:00")

        message = usage_limit_message(
            profile,
            UsageStatus(allowed=False, used=20, limit=20),
            now=now,
        )
        self.assertIn("Wednesday, 09 September at 12:00 AM", message)
        self.assertIn("Africa/Lagos", message)

    def test_follow_up_keyboard_has_copy_action(self) -> None:
        keyboard = follow_up_keyboard(Mode.STUDY)
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("followup:copy", callbacks)
        self.assertIn("📋 COPY", labels)


if __name__ == "__main__":
    unittest.main()
