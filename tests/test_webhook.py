import unittest

from bot.config import Settings
from bot.webhook_runtime import create_webhook_api


def webhook_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TELEGRAM_BOT_TOKEN": "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
        "RUNTIME_MODE": "webhook",
        "TELEGRAM_WEBHOOK_SECRET": "telegram-webhook-secret-123456",
        "TELEGRAM_WEBHOOK_BASE_URL": "https://buildwise.example.test",
        "INTERNAL_CRON_SECRET": "internal-cron-secret-123456789",
    }
    values.update(overrides)
    return Settings(**values)


class WebhookRuntimeTests(unittest.TestCase):
    def test_webhook_routes_are_registered(self) -> None:
        api = create_webhook_api(webhook_settings())
        routes = {route.path for route in api.routes}
        self.assertIn("/health", routes)
        self.assertIn("/telegram/webhook", routes)
        self.assertIn("/internal/reminders", routes)

    def test_webhook_secret_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "TELEGRAM_WEBHOOK_SECRET"):
            create_webhook_api(webhook_settings(TELEGRAM_WEBHOOK_SECRET=None))

    def test_cron_secret_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "INTERNAL_CRON_SECRET"):
            create_webhook_api(webhook_settings(INTERNAL_CRON_SECRET=None))


if __name__ == "__main__":
    unittest.main()
