import unittest

from bot.study_experience import StudyRequestKind, infer_study_answer_kind


class StudentLearningPdfNameTests(unittest.TestCase):
    def test_calculation_sections_infer_worked_solution(self) -> None:
        answer = (
            "📘 Problem\nFind area.\n\n"
            "1️⃣ Given Data\nL = 8 m\nW = 5 m\n\n"
            "2️⃣ Formula / Principle\nArea = L × W\n\n"
            "4️⃣ Substitution\nArea = 8 × 5\n\n"
            "✅ Final Answer\n40 m²"
        )
        kind, _ = infer_study_answer_kind(answer)
        self.assertEqual(kind, StudyRequestKind.CALCULATION)


if __name__ == "__main__":
    unittest.main()
