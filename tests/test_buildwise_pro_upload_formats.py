from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from docx import Document

from bot.config import Settings
from bot.pro.ai_service import ProFile, ProfessionalReportService
from bot.pro.files import ProFileValidationError, detect_pro_file_type
from bot.pro.telegram_handlers import DRAWING, NOTES, TelegramProHandlers


AUTHORIZED_ID = 10001
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=str(AUTHORIZED_ID),
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=2,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=1,
    )


def docx_bytes(text: str = "Project document test content") -> bytes:
    stream = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(stream)
    return stream.getvalue()


class FakeTelegramFile:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeBot:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def get_file(self, file_id: str):
        return FakeTelegramFile(self.files[file_id])


def context(files: dict[str, bytes]):
    return SimpleNamespace(user_data={}, bot=FakeBot(files))


def message(document):
    return SimpleNamespace(document=document, photo=[], reply_text=AsyncMock())


def update(msg):
    return SimpleNamespace(
        effective_message=msg,
        effective_user=SimpleNamespace(id=AUTHORIZED_ID),
        effective_chat=SimpleNamespace(id=999),
    )


class BuildWiseProUploadFormatTests(unittest.TestCase):
    def test_project_source_accepts_pdf_docx_txt_and_existing_images(self) -> None:
        self.assertEqual(detect_pro_file_type("source.pdf", b"%PDF-1.7 test"), "application/pdf")
        self.assertEqual(detect_pro_file_type("source.docx", docx_bytes()), DOCX_MIME)
        self.assertEqual(detect_pro_file_type("source.txt", b"factual source text"), "text/plain")
        self.assertEqual(
            detect_pro_file_type("source.png", b"\x89PNG\r\n\x1a\nrest"), "image/png"
        )
        self.assertEqual(
            detect_pro_file_type("source.jpg", b"\xff\xd8\xffrest"), "image/jpeg"
        )

    def test_company_template_accepts_pdf_docx_txt_and_markdown(self) -> None:
        self.assertEqual(
            detect_pro_file_type("template.docx", docx_bytes("Company Findings"), template=True),
            DOCX_MIME,
        )
        self.assertEqual(
            detect_pro_file_type("template.txt", b"Executive Summary\nFindings", template=True),
            "text/plain",
        )
        self.assertEqual(
            detect_pro_file_type("template.md", b"# Report\n## Findings", template=True),
            "text/markdown",
        )

    def test_fake_docx_is_rejected(self) -> None:
        with self.assertRaises(ProFileValidationError):
            detect_pro_file_type("source.docx", b"PKnot-a-real-docx")

    def test_docx_is_extracted_as_untrusted_input_text(self) -> None:
        content = ProfessionalReportService._file_content(
            ProFile("source.docx", DOCX_MIME, docx_bytes("Measured value not verified")),
            label="project document",
        )
        self.assertEqual(content["type"], "input_text")
        self.assertIn("Measured value not verified", content["text"])
        self.assertNotIn("file_data", content)


class BuildWiseProTelegramUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_drawing_step_accepts_docx(self) -> None:
        data = docx_bytes("Project evidence")
        handlers = TelegramProHandlers(settings())
        ctx = context({"docx": data})
        doc = SimpleNamespace(
            file_name="project.docx",
            file_id="docx",
            file_size=len(data),
        )
        result = await handlers.drawing(update(message(doc)), ctx)
        self.assertEqual(result, NOTES)
        stored = ctx.user_data["buildwise_pro_report_workflow"]["drawing"]
        self.assertEqual(stored.mime_type, DOCX_MIME)

    async def test_drawing_step_accepts_txt(self) -> None:
        data = b"Synthetic factual project source."
        handlers = TelegramProHandlers(settings())
        ctx = context({"txt": data})
        doc = SimpleNamespace(
            file_name="project.txt",
            file_id="txt",
            file_size=len(data),
        )
        result = await handlers.drawing(update(message(doc)), ctx)
        self.assertEqual(result, NOTES)
        stored = ctx.user_data["buildwise_pro_report_workflow"]["drawing"]
        self.assertEqual(stored.mime_type, "text/plain")


if __name__ == "__main__":
    unittest.main()
