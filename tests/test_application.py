import unittest

import httpx

from bot.api import RuntimeHealth, create_api
from bot.app import build_components
from bot.config import Settings


def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


class ApplicationTests(unittest.TestCase):
    def test_application_builds_with_job_queue_and_handlers(self) -> None:
        components = build_components(test_settings())
        handler_count = sum(
            len(group) for group in components.telegram.handlers.values()
        )
        self.assertIsNotNone(components.telegram.job_queue)
        self.assertGreaterEqual(handler_count, 20)


class HealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoint(self) -> None:
        transport = httpx.ASGITransport(
            app=create_api(RuntimeHealth(ready=True), test_settings())
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
