import unittest

from bot.domain import Mode
from bot.formatting import format_response


class StudentLearningFallbackTests(unittest.TestCase):
    def test_unstructured_study_answer_keeps_generic_header(self) -> None:
        result = format_response(Mode.STUDY, "A short direct answer.")
        self.assertIn("BUILDWISE · STUDY & SOLVE", result)


if __name__ == "__main__":
    unittest.main()
