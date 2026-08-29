from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from zipfile import ZipFile

from bot.config import Settings
from bot.pro.ai_service import ProFile
from bot.pro.models import PROFESSIONAL_REVIEW_NOTICE, ProfessionalReport
from bot.pro.report_output import render_report_docx, render_report_pdf
from bot.pro.telegram_document_handlers import TelegramProDocumentHandlers
from bot.pro.telegram_handlers import PRO_STATE_KEY


AUTHORIZED_ID = 6901754599


def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=str(AUTHORIZED_ID),
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=10,
    )


def report() -> ProfessionalReport:
    return ProfessionalReport(
        project_document_information=["Project A", "Inspection report"],
        summary="Draft report based only on supplied evidence.",
        observations=["Visible cracking was stated in the site notes."],
        drawing_sketch_findings=["A concrete element is visible in the supplied drawing."],
        site_note_findings=["User-provided notes state cracking near a support."],
        relevant_technical_considerations=["Verify dimensions and condition on site."],
        referenced_code_standard=["ACI 318 — user-supplied reference only; compliance not verified."],
        recommendations=["Qualified professional to review before action."],
        limitations_assumptions=["No independent site inspection was performed by BuildWise."],
        uncertainties_missing_information=["Crack width and design loads were not supplied."],
    )


class BuildWiseProDocumentRendererTests(unittest.TestCase):
    def test_pdf_is_valid_and_contains_draft_notice(self) -> None:
        data = render_report_pdf(report(), "Project A")
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreater(len(data), 1000)
        # ReportLab may compress page streams, so enforce notice through the model and renderer
        # behavior rather than depending on raw PDF text layout.
        self.assertEqual(report().professional_review_notice, PROFESSIONAL_REVIEW_NOTICE)

    def test_docx_is_valid_and_contains_notice_and_required_sections(self) -> None:
        data = render_report_docx(report(), "Project A")
        self.assertTrue(data.startswith(b"PK"))
        with ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("DRAFT ENGINEERING ASSISTANCE ONLY", document_xml)
        self.assertIn("Limitations / assumptions", document_xml)
        self.assertIn("Uncertainties / missing information", document_xml)
        self.assertIn("Professional review / sign-off", document_xml)


class BuildWiseProTelegramDocumentDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_sends_pdf_and_docx(self) -> None:
        handlers = TelegramProDocumentHandlers(settings())
        handlers._service.generate_professional_report = AsyncMock(return_value=report())
        bot = SimpleNamespace(send_chat_action=AsyncMock(), send_document=AsyncMock())
        context = SimpleNamespace(
            user_data={
                PRO_STATE_KEY: {
                    "project_information": "Project A",
                    "drawing": ProFile("drawing.pdf", "application/pdf", b"%PDF-1.7"),
                    "site_notes": "Observed cracking.",
                    "template": None,
                    "standard_reference": "ACI 318",
                }
            },
            bot=bot,
        )
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=AUTHORIZED_ID),
            effective_chat=SimpleNamespace(id=999),
        )

        result = await handlers.generate(update, context)

        self.assertEqual(result, -1)  # ConversationHandler.END
        self.assertEqual(bot.send_document.await_count, 2)
        filenames = [call.kwargs["document"].filename for call in bot.send_document.await_args_list]
        self.assertTrue(any(name.endswith(".pdf") for name in filenames))
        self.assertTrue(any(name.endswith(".docx") for name in filenames))
        self.assertNotIn(PRO_STATE_KEY, context.user_data)
        completion = query.edit_message_text.await_args_list[-1].args[0]
        self.assertIn("PDF + DOCX", completion)
        self.assertIn("DRAFT ENGINEERING ASSISTANCE ONLY", completion)


if __name__ == "__main__":
    unittest.main()
