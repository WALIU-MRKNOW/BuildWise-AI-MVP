import unittest

from bot.domain import Mode, UserProfile
from bot.keyboards import follow_up_keyboard


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

    def test_study_keyboard_is_copy_and_pdf_only(self) -> None:
        markup = follow_up_keyboard(Mode.STUDY)
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual([button.text for button in buttons], ["📋 Copy", "📄 PDF"])
        self.assertEqual([button.callback_data for button in buttons], ["followup:copy", "followup:pdf"])
        self.assertEqual(len(buttons), 2)

    def test_non_study_keyboard_has_at_most_two_primary_actions(self) -> None:
        markup = follow_up_keyboard(Mode.BUILDING)
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0].text, "📋 Copy")
        self.assertEqual(buttons[1].text, "🔄 Analyze Another")


if __name__ == "__main__":
    unittest.main()
