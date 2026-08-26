import unittest
from unittest.mock import patch

from bot.config import Settings
from bot.domain import QuizQuestion
from bot.services.openai_service import AIServiceError
from bot.webmcp.service import PracticeQuizInput, WebMCPQuizService, WebMCPToolError


def webmcp_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        WEBMCP_ENABLED=True,
        WEBMCP_ACCESS_KEY="preview-access-key-1234567890",
        WEBMCP_SESSION_SECRET="session-secret-abcdefghijklmnopqrstuvwxyz-123456",
        DAILY_AI_REQUEST_LIMIT=20,
    )


class FakeDatabase:
    instances = []

    def __init__(self, settings: Settings) -> None:
        self.calls = []
        self.responses = {
            "consume_webmcp_usage": [
                {"allowed": True, "used": 1, "limit_value": 20, "reservation_id": "res-1"}
            ],
            "finalize_webmcp_usage": True,
            "refund_webmcp_usage": True,
        }
        FakeDatabase.instances.append(self)

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))

    async def close(self) -> None:
        self.calls.append(("close", None))

    async def _rpc(self, name: str, payload: dict):
        self.calls.append((name, payload))
        return self.responses[name]


class FakeAI:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def generate_quiz(self, *, topic: str, difficulty: str, count: int):
        if self.fail:
            raise AIServiceError("quiz failed")
        return [
            QuizQuestion(
                question="Question?",
                options=("A", "B", "C", "D"),
                correct_index=0,
                explanation="Because A.",
            )
            for _ in range(count)
        ]


class WebMCPServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_consumes_and_finalizes_usage(self) -> None:
        FakeDatabase.instances.clear()
        with patch("bot.webmcp.service.SupabaseService", FakeDatabase):
            service = WebMCPQuizService(webmcp_settings())
            service._ai = FakeAI()  # type: ignore[assignment]
            result = await service.generate(
                PracticeQuizInput(topic="reinforced concrete", count=2),
                subject_hash="a" * 64,
            )
        names = [name for name, _ in FakeDatabase.instances[-1].calls]
        self.assertIn("consume_webmcp_usage", names)
        self.assertIn("finalize_webmcp_usage", names)
        self.assertNotIn("refund_webmcp_usage", names)
        self.assertEqual(len(result.questions), 2)
        self.assertEqual(result.usage.remaining, 19)

    async def test_ai_failure_refunds_exact_reservation(self) -> None:
        FakeDatabase.instances.clear()
        with patch("bot.webmcp.service.SupabaseService", FakeDatabase):
            service = WebMCPQuizService(webmcp_settings())
            service._ai = FakeAI(fail=True)  # type: ignore[assignment]
            with self.assertRaises(WebMCPToolError) as raised:
                await service.generate(
                    PracticeQuizInput(topic="reinforced concrete"),
                    subject_hash="b" * 64,
                )
        calls = FakeDatabase.instances[-1].calls
        refund_calls = [payload for name, payload in calls if name == "refund_webmcp_usage"]
        self.assertEqual(refund_calls, [{"p_reservation_id": "res-1"}])
        self.assertEqual(raised.exception.status_code, 502)

    async def test_limit_exhaustion_does_not_call_ai(self) -> None:
        class LimitedDatabase(FakeDatabase):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.responses["consume_webmcp_usage"] = [
                    {"allowed": False, "used": 20, "limit_value": 20, "reservation_id": None}
                ]

        with patch("bot.webmcp.service.SupabaseService", LimitedDatabase):
            service = WebMCPQuizService(webmcp_settings())
            service._ai = FakeAI()  # type: ignore[assignment]
            with self.assertRaises(WebMCPToolError) as raised:
                await service.generate(
                    PracticeQuizInput(topic="reinforced concrete"),
                    subject_hash="c" * 64,
                )
        self.assertEqual(raised.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
