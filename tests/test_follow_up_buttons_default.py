import unittest

from bot.domain import UserProfile


class FollowUpButtonsDefaultTests(unittest.TestCase):
    def test_legacy_disabled_preference_still_enables_buttons(self) -> None:
        profile = UserProfile.from_row(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "telegram_id": 123456,
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


if __name__ == "__main__":
    unittest.main()
