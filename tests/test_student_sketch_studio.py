"""Safety and routing checks for the student Sketch Studio pilot."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.handlers.student_sketch_handlers import StudentSketchHandlers, pilot_access_enabled
from bot.services.student_sketch_service import StudentSketchError, StudentSketchService, sketch_generation_prompt


def test_settings(**overrides):
    values = {
        "TELEGRAM_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test_placeholder",
        **overrides,
    }
    return Settings(**values)


class SketchPilotTests(unittest.TestCase):
    def test_off_by_default_and_explicit_allowlist(self):
        self.assertFalse(pilot_access_enabled(test_settings(), 123))
        self.assertFalse(pilot_access_enabled(
            test_settings(STUDENT_SKETCH_ENABLED=True), 123
        ))
        self.assertTrue(pilot_access_enabled(
            test_settings(
                STUDENT_SKETCH_ENABLED=True,
                STUDENT_SKETCH_ALLOWED_IDS="123",
            ),
            123,
        ))
        self.assertFalse(pilot_access_enabled(
            test_settings(
                STUDENT_SKETCH_ENABLED=True,
                STUDENT_SKETCH_ALLOWED_IDS="123",
            ),
            456,
        ))

    def test_prompt_is_for_original_educational_drawings(self):
        prompt = sketch_generation_prompt("Draw a raft foundation section")
        self.assertIn("raft foundation", prompt)
        self.assertIn("NOT TO SCALE", prompt)
        self.assertIn("Do not invent measured dimensions", prompt)

    def test_empty_or_oversized_sketch_request_is_rejected(self):
        for request in ("   ", "x" * 801):
            with self.assertRaises(ValueError):
                sketch_generation_prompt(request)

    def test_sketch_daily_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            test_settings(STUDENT_SKETCH_DAILY_LIMIT=100)


class SketchImageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_generated_image_bytes(self):
        service = StudentSketchService(test_settings())
        image = b"test-image-bytes"
        service._client.images.generate = AsyncMock(
            return_value=SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(image).decode())]
            )
        )
        self.assertEqual(await service.draw("Draw a footing section"), image)
        kwargs = service._client.images.generate.await_args.kwargs
        self.assertEqual(kwargs["n"], 1)
        self.assertEqual(kwargs["quality"], "medium")

    async def test_missing_image_is_rejected(self):
        service = StudentSketchService(test_settings())
        service._client.images.generate = AsyncMock(
            return_value=SimpleNamespace(data=[])
        )
        with self.assertRaises(StudentSketchError):
            await service.draw("Draw a footing section")


class SketchCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_user_does_not_use_openai_or_daily_quota(self):
        settings = test_settings(STUDENT_SKETCH_ENABLED=True, STUDENT_SKETCH_ALLOWED_IDS="123")
        database = SimpleNamespace(upsert_user=AsyncMock(), consume_usage=AsyncMock())
        handlers = StudentSketchHandlers(settings, database)
        handlers._sketches.draw = AsyncMock()
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=456),
        )
        context = SimpleNamespace(args=["Draw", "foundation"], user_data={})
        await handlers.draw(update, context)
        self.assertIn("not available", message.reply_text.await_args.args[0])
        database.upsert_user.assert_not_awaited()
        handlers._sketches.draw.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
