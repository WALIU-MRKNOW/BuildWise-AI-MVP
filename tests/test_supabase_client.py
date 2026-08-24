import unittest

import httpx

from bot.config import Settings
from bot.services.supabase_service import SupabaseService, _auth_headers


def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


class SupabaseClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/rpc/claim_telegram_update"):
                return httpx.Response(200, json=True)
            if request.url.path.endswith("/rpc/upsert_telegram_user"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "00000000-0000-0000-0000-000000000001",
                            "telegram_id": 123,
                            "username": "student",
                            "display_name": "Test Student",
                            "timezone": "UTC",
                            "timezone_confirmed": False,
                            "timezone_prompted": False,
                            "answer_style": "concise",
                            "follow_up_buttons": True,
                            "save_history": True,
                            "default_daily_goal": 5,
                        }
                    ],
                )
            return httpx.Response(404, json={"message": "not found"})

        self.service = SupabaseService(test_settings())
        self.service._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="https://example.supabase.co",
            transport=httpx.MockTransport(handler),
        )

    async def asyncTearDown(self) -> None:
        await self.service.close()

    async def test_claim_update_uses_atomic_rpc(self) -> None:
        self.assertTrue(await self.service.claim_update(99))

    async def test_profile_is_created_through_rpc(self) -> None:
        profile = await self.service.upsert_user(123, "student", "Test Student")
        self.assertEqual(profile.telegram_id, 123)
        self.assertEqual(profile.default_daily_goal, 5)


class SupabaseHeaderTests(unittest.TestCase):
    def test_new_secret_key_is_not_sent_as_bearer_token(self) -> None:
        headers = _auth_headers("sb_secret_test_placeholder")
        self.assertEqual(headers, {"apikey": "sb_secret_test_placeholder"})

    def test_legacy_service_key_remains_compatible(self) -> None:
        headers = _auth_headers("legacy-jwt-placeholder")
        self.assertEqual(headers["apikey"], "legacy-jwt-placeholder")
        self.assertEqual(headers["Authorization"], "Bearer legacy-jwt-placeholder")


if __name__ == "__main__":
    unittest.main()
