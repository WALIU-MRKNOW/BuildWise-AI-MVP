import unittest

from bot.study_experience import StudyRequestKind, detect_study_request_kind


class StudentTopicClassificationTests(unittest.TestCase):
    def test_construction_theory_questions_remain_topic_explanations(self) -> None:
        prompts = [
            "Explain reinforced concrete in simple terms.",
            "What is a foundation and why is it important?",
            "Discuss the causes of dampness in buildings.",
            "Explain bending moment in a beam.",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    detect_study_request_kind(prompt),
                    StudyRequestKind.TOPIC,
                )


if __name__ == "__main__":
    unittest.main()
