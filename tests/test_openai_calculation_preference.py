import unittest

from bot.study_experience import StudyRequestKind, detect_study_request_kind


class OpenAICalculationPreferenceTests(unittest.TestCase):
    def test_multi_field_construction_math_is_calculation(self) -> None:
        prompts = [
            "Calculate the concrete volume for a 6 m by 4 m slab 150 mm thick.",
            "How many blocks are needed for a 12 m wall that is 3 m high?",
            "Determine the gradient between levels 102.4 m and 98.2 m over 35 m.",
            "Estimate the cost of 250 m2 of plastering at 4200 per m2.",
            "Calculate discharge for a pipe carrying 0.03 m3/s.",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    detect_study_request_kind(prompt),
                    StudyRequestKind.CALCULATION,
                )


if __name__ == "__main__":
    unittest.main()
