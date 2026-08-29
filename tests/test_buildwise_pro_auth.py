import unittest

from bot.config import Settings
from bot.pro.auth import (
    PreviewUsageLimiter,
    access_key_matches,
    csrf_matches,
    csrf_token,
    issue_session,
    stable_subject_hash,
    validate_session,
)


def pro_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_ACCESS_KEY="pro-preview-access-key-123456789",
        BUILDWISE_PRO_SESSION_SECRET="pro-session-secret-abcdefghijklmnopqrstuvwxyz-123456",
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=2,
    )


class BuildWiseProAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_access_session_and_csrf(self) -> None:
        settings = pro_settings()
        self.assertTrue(access_key_matches(settings, "pro-preview-access-key-123456789"))
        self.assertFalse(access_key_matches(settings, "wrong"))
        session = issue_session(settings, now=1000)
        self.assertTrue(validate_session(settings, session, now=1001))
        self.assertFalse(validate_session(settings, session, now=1000 + 28800))
        token = csrf_token(settings, session)
        self.assertTrue(csrf_matches(settings, session, token))
        self.assertFalse(csrf_matches(settings, session, "wrong"))

    def test_subject_hash_is_non_reversible_identifier(self) -> None:
        settings = pro_settings()
        value = stable_subject_hash(settings)
        self.assertEqual(len(value), 64)
        self.assertNotIn("pro-preview-access-key", value)

    async def test_usage_limit_and_exact_refund(self) -> None:
        limiter = PreviewUsageLimiter()
        subject = "a" * 64
        from datetime import date

        today = date(2026, 8, 29)
        self.assertEqual(await limiter.reserve(subject, today, 2), (True, 1))
        self.assertEqual(await limiter.reserve(subject, today, 2), (True, 2))
        self.assertEqual(await limiter.reserve(subject, today, 2), (False, 2))
        self.assertEqual(await limiter.refund(subject, today), 1)
        self.assertEqual(await limiter.reserve(subject, today, 2), (True, 2))


if __name__ == "__main__":
    unittest.main()
