import os
import unittest
from unittest.mock import patch

from bot.config import Settings
from bot.webhook_runtime import _webhook_url


def settings(**overrides: object) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
        "RUNTIME_MODE": "webhook",
        "TELEGRAM_WEBHOOK_SECRET": "w" * 32,
        "INTERNAL_CRON_SECRET": "c" * 32,
    }
    values.update(overrides)
    return Settings(**values)


class WebhookUrlTests(unittest.TestCase):
    def test_explicit_webhook_base_url_wins(self) -> None:
        value = _webhook_url(
            settings(TELEGRAM_WEBHOOK_BASE_URL="https://bot.example.com/")
        )
        self.assertEqual(value, "https://bot.example.com/telegram/webhook")

    def test_railway_public_domain_is_used_when_base_url_is_blank(self) -> None:
        with patch.dict(os.environ, {"RAILWAY_PUBLIC_DOMAIN": "buildwise.example.railway.app"}):
            value = _webhook_url(settings())
        self.assertEqual(
            value,
            "https://buildwise.example.railway.app/telegram/webhook",
        )

    def test_webhook_requires_public_https_origin(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                _webhook_url(settings())


if __name__ == "__main__":
    unittest.main()
