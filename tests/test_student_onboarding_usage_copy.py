import unittest
from pathlib import Path

from bot.config import Settings
from bot.domain import Mode, UserProfile
from bot.handlers.navigation_bot_handlers import NavigationBotHandlers
from bot.keyboards import follow_up_keyboard
from bot.services.reward_usage_service import RewardAwareSupabaseService


def settings(**overrides: str) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
    }
    values.update(overrides)
    return Settings(**values)


def profile(telegram_id: int, *, timezone: str = "Africa/Lagos") -> UserProfile:
    return UserProfile.from_row(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "telegram_id": telegram_id,
            "username": "student",
            "display_name": "Student",
            "timezone": timezone,
            "timezone_confirmed": True,
            "timezone_prompted": True,
            "answer_style": "concise",
            "follow_up_buttons": True,
            "save_history": True,
            "default_daily_goal": 5,
            "student_name": "Waliu",
            "university": "Yaba College of Technology",
            "onboarding_completed": True,
        }
    )


class StudentUsageTierTests(unittest.TestCase):
    def test_founder_gets_high_stress_test_limit(self) -> None:
        service = RewardAwareSupabaseService(
            settings(
                BUILDWISE_FOUNDER_TELEGRAM_IDS="6901754599",
                BUILDWISE_FOUNDER_DAILY_AI_REQUEST_LIMIT="1000",
            )
        )
        self.assertEqual(
            service._effective_student_limit(profile(6901754599), 20),  # type: ignore[attr-defined]
            1000,
        )

    def test_pilot_gets_higher_temporary_limit(self) -> None:
        service = RewardAwareSupabaseService(
            settings(
                BUILDWISE_PILOT_TELEGRAM_IDS="111,222",
                BUILDWISE_PILOT_DAILY_AI_REQUEST_LIMIT="50",
            )
        )
        self.assertEqual(
            service._effective_student_limit(profile(111), 20),  # type: ignore[attr-defined]
            50,
        )

    def test_normal_student_keeps_public_limit(self) -> None:
        service = RewardAwareSupabaseService(
            settings(BUILDWISE_FOUNDER_TELEGRAM_IDS="6901754599")
        )
        self.assertEqual(
            service._effective_student_limit(profile(123), 20),  # type: ignore[attr-defined]
            20,
        )

    def test_pro_limit_setting_is_unchanged_and_separate(self) -> None:
        configured = settings(
            BUILDWISE_FOUNDER_TELEGRAM_IDS="6901754599",
            BUILDWISE_PRO_DAILY_REQUEST_LIMIT="10",
        )
        self.assertEqual(configured.buildwise_pro_daily_request_limit, 10)


class StudentLimitMessageTests(unittest.TestCase):
    def test_limit_message_includes_exact_reset_time_and_timezone(self) -> None:
        text = NavigationBotHandlers._usage_limit_message(profile(123), 20)
        self.assertIn("Daily AI limit reached", text)
        self.assertIn("12:00 AM", text)
        self.assertIn("Africa/Lagos", text)
        self.assertIn("Reset:", text)


class StudentCopyButtonTests(unittest.TestCase):
    def test_study_followups_include_copy_button(self) -> None:
        markup = follow_up_keyboard(Mode.STUDY)
        buttons = [button for row in markup.inline_keyboard for button in row]
        matches = [button for button in buttons if button.text == "COPY"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].callback_data, "followup:copy")

    def test_analysis_followups_include_copy_button(self) -> None:
        markup = follow_up_keyboard(Mode.BUILDING)
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertTrue(any(button.text == "COPY" for button in buttons))


class StudentOnboardingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path("migrations/006_student_onboarding_profile.sql").read_text(
            encoding="utf-8"
        ).lower()

    def test_required_onboarding_fields_are_persisted(self) -> None:
        self.assertIn("student_name text", self.sql)
        self.assertIn("university text", self.sql)
        self.assertIn("onboarding_completed boolean", self.sql)
        self.assertIn("onboarding_completed_at timestamptz", self.sql)

    def test_new_onboarding_flag_defaults_to_incomplete(self) -> None:
        self.assertIn("onboarding_completed boolean not null default false", self.sql)


if __name__ == "__main__":
    unittest.main()
