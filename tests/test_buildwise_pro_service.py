import json
from types import SimpleNamespace
import unittest

from bot.config import Settings
from bot.pro.ai_service import ProFile, ProfessionalReportService
from bot.pro.models import PROFESSIONAL_REVIEW_NOTICE, ProfessionalReportRequest


def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        output = {
            "project_document_information": ["Project A", "Drawing A01"],
            "summary": "Draft inspection summary based on supplied material.",
            "observations": ["Cracking was recorded in the supplied site notes."],
            "drawing_sketch_findings": ["The drawing shows a reinforced concrete element."],
            "site_note_findings": ["Site notes report visible cracking."],
            "relevant_technical_considerations": ["Verify dimensions and reinforcement on site."],
            "referenced_code_standard": ["AS 3600 — user-supplied reference; clause compliance not verified."],
            "recommendations": ["Qualified engineer to inspect and verify before action."],
            "limitations_assumptions": ["No independent site inspection was performed."],
            "uncertainties_missing_information": ["Crack width was not supplied."],
            "review_signoff": "Prepared as an AI-assisted draft for qualified professional review.",
            "professional_review_notice": "model text should be replaced",
            "template_structure_used": True,
        }
        return SimpleNamespace(output_text=json.dumps(output))


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class BuildWiseProServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_report_uses_existing_adapter_configuration(self) -> None:
        service = ProfessionalReportService(settings())
        fake = FakeClient()
        service._client = fake  # type: ignore[assignment]
        report = await service.generate_professional_report(
            request=ProfessionalReportRequest(
                project_information="Project A / Drawing A01",
                site_notes="Visible cracking noted near the support.",
                standard_reference="AS 3600",
            ),
            drawing=ProFile("drawing.pdf", "application/pdf", b"%PDF-1.7 test"),
            template=ProFile("template.pdf", "application/pdf", b"%PDF-1.7 template"),
        )
        self.assertEqual(report.professional_review_notice, PROFESSIONAL_REVIEW_NOTICE)
        self.assertTrue(report.template_structure_used)
        self.assertIn("Crack width was not supplied", report.uncertainties_missing_information[0])
        call = fake.responses.kwargs
        self.assertIsNotNone(call)
        self.assertEqual(call["model"], settings().openai_model)
        self.assertFalse(call["store"])
        user_content = call["input"][0]["content"]
        prompt = user_content[0]["text"]
        self.assertIn("Never invent dimensions", prompt)
        self.assertIn("Do not quote or fabricate clauses", prompt)
        self.assertEqual(sum(1 for item in user_content if item["type"] == "input_file"), 2)


if __name__ == "__main__":
    unittest.main()
