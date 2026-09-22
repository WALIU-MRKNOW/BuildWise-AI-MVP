import unittest
from types import SimpleNamespace

from bot.services.openai_service import OpenAIService


class QuizParsingTests(unittest.TestCase):
    def test_json_fences_are_tolerated(self) -> None:
        data = OpenAIService._parse_json('```json\n{"questions": []}\n```')
        self.assertEqual(data, {"questions": []})

    def test_valid_question_is_parsed(self) -> None:
        question = OpenAIService._parse_question(
            {
                "question": "2 + 2?",
                "options": ["1", "2", "3", "4"],
                "correct_index": 3,
                "explanation": "Two plus two is four.",
            }
        )
        self.assertEqual(question.correct_index, 3)
        self.assertEqual(question.options[3], "4")

    def test_parse_quiz_response_requires_exact_requested_count(self) -> None:
        payload = '{"questions":[{"question":"Q?","options":["a","b","c","d"],"correct_index":0,"explanation":"Because."}]}'
        with self.assertRaises(ValueError):
            OpenAIService._parse_quiz_response(payload, count=3)

    def test_parse_quiz_response_accepts_exact_requested_count(self) -> None:
        payload = '{"questions":[{"question":"Q?","options":["a","b","c","d"],"correct_index":0,"explanation":"Because."}]}'
        questions = OpenAIService._parse_quiz_response(payload, count=1)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].question, "Q?")

    def test_question_requires_four_options(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIService._parse_question(
                {
                    "question": "Bad",
                    "options": ["one"],
                    "correct_index": 0,
                    "explanation": "Bad",
                }
            )

    def test_quiz_answers_are_balanced_even_if_model_returns_all_a(self) -> None:
        original = [
            OpenAIService._parse_question(
                {
                    "question": f"Question {index}",
                    "options": [
                        f"correct-{index}",
                        f"wrong-b-{index}",
                        f"wrong-c-{index}",
                        f"wrong-d-{index}",
                    ],
                    "correct_index": 0,
                    "explanation": f"Explanation {index}",
                }
            )
            for index in range(10)
        ]

        balanced = OpenAIService._balance_quiz_answers(original)
        indices = [question.correct_index for question in balanced]
        counts = [indices.count(index) for index in range(4)]

        self.assertEqual(set(indices), {0, 1, 2, 3})
        self.assertLessEqual(max(counts) - min(counts), 1)
        for before, after in zip(original, balanced, strict=True):
            correct_answer = before.options[before.correct_index]
            self.assertEqual(after.options[after.correct_index], correct_answer)
            self.assertEqual(set(after.options), set(before.options))
            self.assertEqual(after.explanation, before.explanation)


class _RetryResponses:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(output_text='{"questions":[]}')
        return SimpleNamespace(
            output_text='{"questions":[{"question":"Stress?","options":["a","b","c","d"],"correct_index":1,"explanation":"Because."}]}'
        )


class QuizRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_quiz_retries_invalid_model_output_once(self) -> None:
        service = OpenAIService.__new__(OpenAIService)
        responses = _RetryResponses()
        service._client = SimpleNamespace(responses=responses)
        service._settings = SimpleNamespace(
            openai_model="test-model",
            openai_max_output_tokens=1000,
        )

        questions = await service.generate_quiz(
            topic="stress", difficulty="medium", count=1
        )

        self.assertEqual(len(questions), 1)
        self.assertEqual(len(responses.calls), 2)
        self.assertIn("previous response could not be validated", responses.calls[1]["input"])


if __name__ == "__main__":
    unittest.main()
