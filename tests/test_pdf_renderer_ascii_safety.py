import unittest

from bot.student_solution_pdf import render_student_solution_pdf


class StudentPdfAsciiSafetyTests(unittest.TestCase):
    def test_pdf_handles_phone_math_symbols_and_emoji(self) -> None:
        answer = (
            "📘 Problem\nFind volume.\n\n"
            "1️⃣ Given Data\nL = 6 m\nW = 4 m\nT = 0.15 m\n\n"
            "2️⃣ Formula / Principle\nV = L × W × T\n\n"
            "4️⃣ Substitution\nV = 6 × 4 × 0.15\n\n"
            "5️⃣ Step-by-Step Working\nV = 3.6 m³\n\n"
            "✅ Final Answer\n3.6 m³"
        )
        pdf = render_student_solution_pdf("Calculate slab volume", answer)
        self.assertTrue(pdf.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
