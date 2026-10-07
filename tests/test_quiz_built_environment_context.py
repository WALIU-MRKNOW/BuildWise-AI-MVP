import unittest
from types import SimpleNamespace

from bot.services.ai_metering import UsageMeter
from bot.services.openai_service import OpenAIService


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text=(
                '{"questions":[{"question":"What is the main purpose of steel '
                'reinforcement in reinforced concrete?","options":["Resist tensile '
                'stresses","Increase paint adhesion","Reduce room temperature",'
                '"Replace formwork"],"correct_index":0,"explanation":"Steel '
                'reinforcement mainly carries tensile forces that concrete handles '
                'poorly."}]}'
            )
        )


class QuizBuiltEnvironmentContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_topic_is_scoped_to_built_environment(self) -> None:
        service = OpenAIService.__new__(OpenAIService)
        service._meter = UsageMeter(SimpleNamespace(ai_budget_enabled=False))
        responses = _FakeResponses()
        service._client = SimpleNamespace(responses=responses)
        service._settings = SimpleNamespace(
            openai_model="test-model",
            openai_max_output_tokens=1000,
        )

        questions = await service.generate_quiz(
            topic="reinforcement",
            difficulty="medium",
            count=1,
        )

        self.assertEqual(len(questions), 1)
        self.assertIsNotNone(responses.kwargs)
        instructions = responses.kwargs["instructions"].lower()
        prompt = responses.kwargs["input"].lower()

        self.assertIn("built-environment", instructions)
        self.assertIn("construction", instructions)
        self.assertIn("ambiguous", instructions)
        self.assertIn("reinforcement", instructions)
        self.assertIn("not psychology", instructions)
        self.assertIn("reinforcement", prompt)
        self.assertIn("built-environment scope", prompt)
        self.assertIn("plausible distractors", prompt)
        self.assertIn("common student mistakes", prompt)
        self.assertIn("reasoning and application", prompt)
        self.assertIn("technically unambiguous", prompt)
        self.assertIn("shuffles", prompt)


if __name__ == "__main__":
    unittest.main()
