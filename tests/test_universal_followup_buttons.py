import unittest

from bot.domain import UserProfile
from bot.keyboards import follow_up_keyboard
from bot.domain import Mode


class UniversalFollowUpButtonTests(unittest.TestCase):
    def test_legacy_disabled_preference_is_ignored(self) -> None:
        profile = UserProfile.from_row(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "telegram_id": 12345,
                "username": "student",
                "display_name": "Student",
                "timezone": "UTC",
                "timezone_confirmed": True,
                "timezone_prompted": True,
                "answer_style": "concise",
                "follow_up_buttons": False,
                "save_history": True,
                "default_daily_goal": 5,
            }
        )
        self.assertTrue(profile.follow_up_buttons)

    def test_study_keyboard_contains_copy_and_pdf(self) -> None:
        markup = follow_up_keyboard(Mode.STUDY)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("📋 COPY", labels)
        self.assertIn("📄 Get Solution PDF", labels)


if __name__ == "__main__":
    unittest.main()
