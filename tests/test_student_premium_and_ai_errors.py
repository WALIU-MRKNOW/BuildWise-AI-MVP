import unittest

import httpx
from openai import RateLimitError

from bot.config import Settings
from bot.services.openai_service import OpenAIService


def settings(**overrides) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
    }
    values.update(overrides)
    return Settings(**values)


def rate_limit_error(code: str) -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request)
    return RateLimitError(
        "rate limited",
        response=response,
        body={"error": {"code": code, "type": code}},
    )


class StudentPremiumConfigTests(unittest.TestCase):
    def test_student_premium_defaults_to_3000_and_disabled(self) -> None:
        config = settings()
        self.assertFalse(config.student_premium_enabled)
        self.assertEqual(config.student_premium_price_ngn, 3000)

    def test_student_premium_price_can_be_overridden(self) -> None:
        config = settings(STUDENT_PREMIUM_PRICE_NGN=3500)
        self.assertEqual(config.student_premium_price_ngn, 3500)


class OpenAIErrorClassificationTests(unittest.TestCase):
    def test_insufficient_quota_is_billing_quota(self) -> None:
        error = OpenAIService._translate_error(rate_limit_error("insufficient_quota"))
        self.assertEqual(error.code, "billing_quota")
        self.assertIn("allowance is restored", error.public_message)

    def test_regular_rate_limit_is_temporary(self) -> None:
        error = OpenAIService._translate_error(rate_limit_error("rate_limit_exceeded"))
        self.assertEqual(error.code, "rate_limit")
        self.assertIn("lot of requests", error.public_message)


if __name__ == "__main__":
    unittest.main()
