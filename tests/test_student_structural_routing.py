import unittest

from bot.domain import Mode
from bot.router import RequestRouter


class StudentStructuralRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RequestRouter()

    def test_structural_beam_calculation_text_routes_to_study(self) -> None:
        question = (
            "A simply supported reinforced concrete beam has a span of 6 m. "
            "Calculate the factored UDL, support reactions, maximum shear force, "
            "position of maximum bending moment, and maximum bending moment."
        )

        decision = self.router.route(question)

        self.assertEqual(decision.mode, Mode.STUDY)
        self.assertEqual(decision.reason, "text study/calculation keywords")

    def test_structural_explanation_text_routes_to_study(self) -> None:
        decision = self.router.route(
            "Explain how bending moment and shear force behave in a concrete beam."
        )

        self.assertEqual(decision.mode, Mode.STUDY)

    def test_building_photo_still_routes_to_building(self) -> None:
        decision = self.router.route(
            "Check this concrete beam for visible damage.",
            has_image=True,
        )

        self.assertEqual(decision.mode, Mode.BUILDING)


if __name__ == "__main__":
    unittest.main()
