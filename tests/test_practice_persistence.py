import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.domain import QuizQuestion
from bot.handlers.bot_handlers import BotHandlers
from bot.keyboards import quiz_answer_keyboard
from bot.services.reward_usage_service import StudentAccessSupabaseService


def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


def practice_session():
    return {
        "id": "abc12345",
        "topic": "stress",
        "difficulty": "medium",
        "questions": [
            QuizQuestion(
                question="What is stress?",
                options=("Force/area", "Force x area", "Area/force", "None"),
                correct_index=0,
                explanation="Stress is force divided by area.",
            )
        ],
        "index": 0,
        "correct": 0,
        "answered": False,
        "mistakes": [],
    }


class PracticePersistenceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_practice_session_replaces_older_copy(self) -> None:
        service = StudentAccessSupabaseService(settings())
        service._request = AsyncMock(
            side_effect=[[{"id": "new-row"}], None]
        )

        await service.save_practice_session("user-1", practice_session())

        first = service._request.await_args_list[0]
        self.assertEqual(first.args[:2], ("POST", "/rest/v1/saved_items"))
        payload = json.loads(first.kwargs["json"]["content"])
        self.assertEqual(payload["id"], "abc12345")
        self.assertEqual(payload["questions"][0]["correct_index"], 0)

        second = service._request.await_args_list[1]
        self.assertEqual(second.args[:2], ("DELETE", "/rest/v1/saved_items"))
        self.assertEqual(second.kwargs["params"]["id"], "neq.new-row")

    async def test_load_practice_session_rehydrates_questions(self) -> None:
        service = StudentAccessSupabaseService(settings())
        serialized = {
            "id": "abc12345",
            "topic": "stress",
            "difficulty": "medium",
            "questions": [
                {
                    "question": "What is stress?",
                    "options": ["Force/area", "Force x area", "Area/force", "None"],
                    "correct_index": 0,
                    "explanation": "Stress is force divided by area.",
                }
            ],
            "index": 0,
            "correct": 0,
            "answered": False,
            "mistakes": [],
        }
        service._request = AsyncMock(
            return_value=[{"content": json.dumps(serialized)}]
        )

        session = await service.load_practice_session("user-1")

        self.assertIsNotNone(session)
        self.assertIsInstance(session["questions"][0], QuizQuestion)
        self.assertEqual(session["questions"][0].options[0], "Force/area")

    async def test_history_hides_internal_practice_session(self) -> None:
        service = StudentAccessSupabaseService(settings())
        service._request = AsyncMock(return_value=[])

        await service.recent_saved_items("user-1")

        params = service._request.await_args.kwargs["params"]
        self.assertIn("practice_session", params["item_type"])
        self.assertIn("student_profile", params["item_type"])


class PracticeRestartRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_button_restores_session_after_restart(self) -> None:
        handler = BotHandlers.__new__(BotHandlers)
        session = practice_session()
        database = SimpleNamespace(
            load_practice_session=AsyncMock(return_value=session),
            save_practice_session=AsyncMock(),
        )
        handler._database = database
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})
        profile = SimpleNamespace(id="user-1")
        callback = quiz_answer_keyboard("abc12345", 0).inline_keyboard[0][0].callback_data

        await handler._handle_practice_callback(
            update, context, profile, callback
        )

        database.load_practice_session.assert_awaited_once_with("user-1")
        database.save_practice_session.assert_awaited_once()
        self.assertTrue(context.user_data["practice_session"]["answered"])
        self.assertEqual(context.user_data["practice_session"]["correct"], 1)


if __name__ == "__main__":
    unittest.main()
