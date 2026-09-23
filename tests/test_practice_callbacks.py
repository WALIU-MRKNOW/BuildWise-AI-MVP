import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.domain import QuizQuestion
from bot.handlers.bot_handlers import BotHandlers
from bot.keyboards import quiz_answer_keyboard, quiz_next_keyboard


class PracticeCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.handler = BotHandlers.__new__(BotHandlers)
        self.handler._database = SimpleNamespace(
            record_quiz_result=AsyncMock(),
            record_study_completion=AsyncMock(return_value=None),
        )
        self.handler._celebrate = AsyncMock()
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.update = SimpleNamespace(effective_message=self.message)
        self.profile = SimpleNamespace(id=123)
        questions = [
            QuizQuestion(
                question=f"Stress question {index + 1}?",
                options=("Correct", "Wrong 1", "Wrong 2", "Wrong 3"),
                correct_index=0,
                explanation="Because.",
            )
            for index in range(3)
        ]
        self.context = SimpleNamespace(user_data={
            "practice_session": {
                "id": "abc12345", "topic": "stress", "difficulty": "medium",
                "questions": questions, "index": 0, "correct": 0,
                "answered": False, "mistakes": [],
            }
        })

    async def test_three_questions_duplicate_taps_and_completion(self) -> None:
        answer = quiz_answer_keyboard("abc12345", 0).inline_keyboard[0][0].callback_data
        next_button = quiz_next_keyboard("abc12345", 0).inline_keyboard[0][0].callback_data
        await self.handler._handle_practice_callback(self.update, self.context, self.profile, answer)
        await self.handler._handle_practice_callback(self.update, self.context, self.profile, answer)
        self.assertEqual(self.context.user_data["practice_session"]["correct"], 1)
        await self.handler._handle_practice_callback(self.update, self.context, self.profile, next_button)
        await self.handler._handle_practice_callback(self.update, self.context, self.profile, answer)
        self.assertFalse(self.context.user_data["practice_session"]["answered"])
        self.assertEqual(self.context.user_data["practice_session"]["index"], 1)

        for index in (1, 2):
            answer = quiz_answer_keyboard("abc12345", index).inline_keyboard[0][0].callback_data
            next_button = quiz_next_keyboard("abc12345", index).inline_keyboard[0][0].callback_data
            await self.handler._handle_practice_callback(self.update, self.context, self.profile, answer)
            await self.handler._handle_practice_callback(self.update, self.context, self.profile, next_button)

        self.assertNotIn("practice_session", self.context.user_data)
        self.handler._database.record_quiz_result.assert_awaited_once_with(
            user_id=123, subject=None, topic="stress", total_questions=3,
            correct_answers=3,
        )
        self.handler._database.record_study_completion.assert_awaited_once()
        self.assertTrue(any(
            "Score: 3 / 3 (100%)" in call.args[0]
            for call in self.message.reply_text.call_args_list
        ))

    async def test_previous_session_buttons_do_not_answer_new_quiz(self) -> None:
        old_answer = quiz_answer_keyboard("oldtoken", 0).inline_keyboard[0][0].callback_data
        await self.handler._handle_practice_callback(self.update, self.context, self.profile, old_answer)
        self.assertFalse(self.context.user_data["practice_session"]["answered"])
        self.message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
