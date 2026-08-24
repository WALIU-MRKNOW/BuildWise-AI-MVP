import unittest

from bot.domain import Mode
from bot.router import RequestRouter


class RequestRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RequestRouter()

    def test_explicit_mode_wins(self) -> None:
        result = self.router.route(
            "Solve this calculation", selected_mode=Mode.SKETCH, has_image=True
        )
        self.assertEqual(result.mode, Mode.SKETCH)

    def test_pdf_defaults_to_study(self) -> None:
        self.assertEqual(self.router.route("", has_pdf=True).mode, Mode.STUDY)

    def test_building_language_is_detected(self) -> None:
        result = self.router.route("Is this wall crack and damp patch serious?", has_image=True)
        self.assertEqual(result.mode, Mode.BUILDING)

    def test_sketch_language_is_detected(self) -> None:
        result = self.router.route("Review circulation in this floor plan", has_image=True)
        self.assertEqual(result.mode, Mode.SKETCH)

    def test_floor_plan_wins_over_generic_building_word(self) -> None:
        result = self.router.route("Analyze this building floor plan", has_image=True)
        self.assertEqual(result.mode, Mode.SKETCH)

    def test_plain_question_defaults_to_study(self) -> None:
        result = self.router.route("Explain Newton's second law")
        self.assertEqual(result.mode, Mode.STUDY)

    def test_unlabelled_image_requests_clarification(self) -> None:
        result = self.router.route("", has_image=True)
        self.assertTrue(result.needs_clarification)


if __name__ == "__main__":
    unittest.main()
