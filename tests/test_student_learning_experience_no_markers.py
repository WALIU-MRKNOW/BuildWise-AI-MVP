import unittest

from bot.domain import Mode
from bot.formatting import format_response
from bot.study_experience import StudyRequestKind, infer_study_answer_kind


class StudentLearningNoMarkerTests(unittest.TestCase):
    def test_clean_calculation_answer_is_inferred_without_hidden_marker(self) -> None:
        answer = (
            "📘 Problem\nFind the concrete volume.\n\n"
            "1️⃣ Given Data\nLength = 6 m\nWidth = 4 m\nThickness = 0.15 m\n\n"
            "2️⃣ Formula / Principle\nVolume = Length × Width × Thickness\n\n"
            "4️⃣ Substitution\nVolume = 6 × 4 × 0.15\n\n"
            "5️⃣ Step-by-Step Working\nVolume = 3.6 m³\n\n"
            "✅ Final Answer\n3.6 m³"
        )
        kind, cleaned = infer_study_answer_kind(answer)
        self.assertEqual(kind, StudyRequestKind.CALCULATION)
        self.assertEqual(cleaned, answer)
        self.assertIn("SOLVING CALCULATION", format_response(Mode.STUDY, answer))


if __name__ == "__main__":
    unittest.main()
