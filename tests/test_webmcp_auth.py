import unittest

from bot.config import Settings
from bot.webmcp.auth import (
    access_key_matches,
    csrf_matches,
    csrf_token,
    issue_session,
    subject_hash,
    validate_session,
)


def webmcp_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        WEBMCP_ENABLED=True,
        WEBMCP_ACCESS_KEY="preview-access-key-1234567890",
        WEBMCP_SESSION_SECRET="session-secret-abcdefghijklmnopqrstuvwxyz-123456",
    )


class WebMCPAuthTests(unittest.TestCase):
    def test_access_key_is_constant_time_compared(self) -> None:
        settings = webmcp_settings()
        self.assertTrue(access_key_matches(settings, "preview-access-key-1234567890"))
        self.assertFalse(access_key_matches(settings, "wrong"))

    def test_session_expiration_and_csrf(self) -> None:
        settings = webmcp_settings()
        token = issue_session(settings, now=1000)
        self.assertTrue(validate_session(settings, token, now=1001))
        self.assertFalse(validate_session(settings, token, now=1000 + 28800))
        csrf = csrf_token(settings, token)
        self.assertTrue(csrf_matches(settings, token, csrf))
        self.assertFalse(csrf_matches(settings, token, "wrong"))

    def test_subject_hash_is_stable_and_not_the_access_key(self) -> None:
        settings = webmcp_settings()
        first = subject_hash(settings)
        second = subject_hash(settings)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotIn("preview-access-key", first)


if __name__ == "__main__":
    unittest.main()
