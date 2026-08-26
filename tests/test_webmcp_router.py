import re
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bot.api import RuntimeHealth, create_api
from bot.config import Settings
from bot.webmcp.service import (
    PracticeQuizOutput,
    QuizQuestionOutput,
    QuizUsageOutput,
)


def webmcp_settings(*, enabled: bool = True) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        WEBMCP_ENABLED=enabled,
        WEBMCP_ACCESS_KEY="preview-access-key-1234567890",
        WEBMCP_SESSION_SECRET="session-secret-abcdefghijklmnopqrstuvwxyz-123456",
    )


class WebMCPRouterTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, *, enabled: bool = True) -> httpx.AsyncClient:
        app = create_api(RuntimeHealth(ready=True), webmcp_settings(enabled=enabled))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            follow_redirects=True,
        )

    async def _login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/app/login",
            content="access_key=preview-access-key-1234567890",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="buildwise-csrf" content="([^"]+)"', response.text)
        self.assertIsNotNone(match)
        return match.group(1)  # type: ignore[union-attr]

    async def test_disabled_preview_does_not_mount_app(self) -> None:
        async with await self._client(enabled=False) as client:
            response = await client.get("/app")
        self.assertEqual(response.status_code, 404)

    async def test_login_and_site_tool_registration(self) -> None:
        async with await self._client() as client:
            page = await client.get("/app")
            self.assertIn("Preview access key", page.text)
            await self._login(client)
            script = await client.get("/app/webmcp.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("document.modelContext.registerTool", script.text)
        self.assertIn("generate_practice_quiz", script.text)
        self.assertNotIn("preview-access-key-1234567890", script.text)
        self.assertNotIn("sk-test-placeholder", script.text)

    async def test_tool_rejects_unauthorized_and_bad_csrf(self) -> None:
        async with await self._client() as client:
            unauthorized = await client.post(
                "/app/api/tools/generate-practice-quiz",
                json={"topic": "reinforced concrete", "count": 5},
            )
            self.assertEqual(unauthorized.status_code, 401)
            await self._login(client)
            forbidden = await client.post(
                "/app/api/tools/generate-practice-quiz",
                json={"topic": "reinforced concrete", "count": 5},
                headers={"X-BuildWise-CSRF": "wrong"},
            )
        self.assertEqual(forbidden.status_code, 403)

    async def test_invalid_input_is_rejected_before_tool_execution(self) -> None:
        async with await self._client() as client:
            csrf = await self._login(client)
            with patch(
                "bot.webmcp.service.WebMCPQuizService.generate",
                new_callable=AsyncMock,
            ) as generate:
                response = await client.post(
                    "/app/api/tools/generate-practice-quiz",
                    json={"topic": "x", "difficulty": "extreme", "count": 50},
                    headers={"X-BuildWise-CSRF": csrf},
                )
        self.assertEqual(response.status_code, 422)
        generate.assert_not_awaited()

    async def test_generate_practice_quiz_returns_structured_output(self) -> None:
        result = PracticeQuizOutput(
            topic="reinforced concrete",
            difficulty="medium",
            count=1,
            questions=[
                QuizQuestionOutput(
                    question="What primarily resists tension in reinforced concrete?",
                    options=["Concrete", "Steel reinforcement", "Water", "Formwork"],
                    correct_index=1,
                    explanation="Steel reinforcement carries tensile forces.",
                )
            ],
            usage=QuizUsageOutput(used=1, limit=20, remaining=19),
        )
        async with await self._client() as client:
            csrf = await self._login(client)
            with patch(
                "bot.webmcp.router.WebMCPQuizService.generate",
                new=AsyncMock(return_value=result),
            ):
                response = await client.post(
                    "/app/api/tools/generate-practice-quiz",
                    json={"topic": "reinforced concrete", "count": 1},
                    headers={"X-BuildWise-CSRF": csrf},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool"], "generate_practice_quiz")
        self.assertEqual(body["questions"][0]["correct_index"], 1)
        self.assertEqual(body["usage"]["remaining"], 19)


if __name__ == "__main__":
    unittest.main()
