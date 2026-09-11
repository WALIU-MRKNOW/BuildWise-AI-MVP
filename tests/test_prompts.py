import unittest

from bot.domain import Mode
from bot.prompts import default_user_request, system_prompt


class PromptTests(unittest.TestCase):
    def test_study_pdf_becomes_study_material(self) -> None:
        prompt = default_user_request(Mode.STUDY, has_image=False, has_pdf=True)
        self.assertIn("study guide", prompt)

    def test_building_prompt_forbids_safety_claim(self) -> None:
        prompt = system_prompt(Mode.BUILDING)
        self.assertIn("Never declare", prompt)
        self.assertIn("Concern Level", prompt)

    def test_sketch_prompt_forbids_approval_claim(self) -> None:
        self.assertIn("Never claim construction", system_prompt(Mode.SKETCH))

    def test_study_prompt_requires_visual_teaching_diagram(self) -> None:
        prompt = system_prompt(Mode.STUDY)
        self.assertIn("BUILDWISE VISUAL TEACHING STANDARD", prompt)
        self.assertIn("📐 Diagram", prompt)
        self.assertIn("every CALCULATION", prompt)
        self.assertIn("TOPIC / THEORY", prompt)

    def test_sketch_prompt_separates_observation_from_assumption(self) -> None:
        prompt = system_prompt(Mode.SKETCH)
        self.assertIn("BUILDWISE SKETCH VISUAL STANDARD", prompt)
        self.assertIn("📐 Diagram", prompt)
        self.assertIn("Separate visible facts from assumptions", prompt)
        self.assertIn("never be described as a measured dimension", prompt)


if __name__ == "__main__":
    unittest.main()
