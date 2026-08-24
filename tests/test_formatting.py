import unittest

from bot.domain import Mode
from bot.formatting import format_response, split_for_telegram


class FormattingTests(unittest.TestCase):
    def test_building_response_always_has_safety_note(self) -> None:
        result = format_response(Mode.BUILDING, "A diagonal crack is visible.")
        self.assertIn("preliminary photo review", result)
        self.assertIn("structural safety", result)

    def test_sketch_response_has_approval_note(self) -> None:
        result = format_response(Mode.SKETCH, "The corridor appears narrow.")
        self.assertIn("not construction, code, structural, or permit approval", result)

    def test_study_header_uses_buildwise_brand(self) -> None:
        self.assertIn("BUILDWISE · STUDY & SOLVE", format_response(Mode.STUDY, "42"))

    def test_long_responses_are_split_under_limit(self) -> None:
        chunks = split_for_telegram("word " * 300, limit=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
