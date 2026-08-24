import unittest

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


if __name__ == "__main__":
    unittest.main()
