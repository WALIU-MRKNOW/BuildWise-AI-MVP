import unittest

from bot.domain import Mode
from bot.formatting import clean_student_math, format_response
from bot.keyboards import follow_up_keyboard
from bot.student_solution_pdf import render_student_solution_pdf
from bot.study_experience import StudyRequestKind, detect_study_request_kind


class StudentLearningExperienceTests(unittest.TestCase):
    def test_hard_construction_calculation_is_detected(self) -> None:
        question = (
            "A simply supported reinforced concrete beam has a span of 6 m and carries "
            "18 kN/m dead load and 12 kN/m imposed load. Calculate the reactions and "
            "maximum bending moment."
        )
        self.assertEqual(
            detect_study_request_kind(question), StudyRequestKind.CALCULATION
        )

    def test_quantity_surveying_calculation_is_detected(self) -> None:
        question = (
            "Calculate the quantity of concrete required for a slab 8 m by 5 m by 150 mm "
            "thick and include 5% wastage."
        )
        self.assertEqual(
            detect_study_request_kind(question), StudyRequestKind.CALCULATION
        )

    def test_theory_question_stays_topic(self) -> None:
        self.assertEqual(
            detect_study_request_kind("Explain bending moment in simple terms."),
            StudyRequestKind.TOPIC,
        )

    def test_calculation_marker_changes_header_and_is_hidden(self) -> None:
        result = format_response(
            Mode.STUDY,
            "[[CALCULATION]]\n📘 Problem\nFind the slab volume.\n\n✅ Final Answer\n3.6 m³",
        )
        self.assertIn("SOLVING CALCULATION", result)
        self.assertNotIn("[[CALCULATION]]", result)

    def test_topic_marker_changes_header_and_is_hidden(self) -> None:
        result = format_response(
            Mode.STUDY,
            "[[TOPIC]]\n📚 Meaning\nA foundation transfers loads to the ground.",
        )
        self.assertIn("EXPLAINING TOPIC", result)
        self.assertNotIn("[[TOPIC]]", result)

    def test_raw_latex_is_cleaned_for_telegram(self) -> None:
        raw = r"\[ \text{Volume} = 6 \times 4 \times 0.15 = 3.6\,m^3 \]"
        cleaned = clean_student_math(raw)
        self.assertNotIn(r"\text", cleaned)
        self.assertNotIn(r"\times", cleaned)
        self.assertNotIn(r"\[", cleaned)
        self.assertIn("×", cleaned)
        self.assertIn("m³", cleaned)

    def test_study_keyboard_has_solution_pdf_action(self) -> None:
        markup = follow_up_keyboard(Mode.STUDY)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("followup:pdf", callbacks)
        self.assertIn("followup:simpler", callbacks)
        self.assertIn("followup:similar", callbacks)

    def test_solution_pdf_is_generated_in_memory(self) -> None:
        pdf = render_student_solution_pdf(
            "Calculate the area of a 6 m by 4 m slab.",
            "[[CALCULATION]]\n📘 Problem\nFind the area.\n\n1️⃣ Given Data\nLength = 6 m\nWidth = 4 m\n\n2️⃣ Formula / Principle\nArea = Length × Width\n\n4️⃣ Substitution\nArea = 6 × 4\n\n✅ Final Answer\nArea = 24 m²",
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
