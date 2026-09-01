from __future__ import annotations

from datetime import date
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bot.config import Settings
from bot.pro.ai_service import ProFile
from bot.pro.models import ProfessionalReport
from bot.pro.telegram_beta_handlers import (
    PRO_FEEDBACK_KEY,
    PRO_SESSION_ID_KEY,
    TelegramProBetaHandlers,
)
from bot.pro.telegram_handlers import PRO_STATE_KEY


TESTER_ID = 10001


def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="abcdefghijklmnop",
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=str(TESTER_ID),
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=1,
    )


def report() -> ProfessionalReport:
    return ProfessionalReport(
        summary="Synthetic reliability test report.",
        limitations_assumptions=["Synthetic test only."],
        uncertainties_missing_information=["No verified dimensions supplied."],
    )


def callback_update(query, *, user_id: int = TESTER_ID):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="tester", first_name="Test"),
        effective_message=getattr(query, "message", None),
        effective_chat=SimpleNamespace(id=777),
        callback_query=query,
    )


class ProReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.env = patch.dict(os.environ, {"BUILDWISE_ADMIN_TELEGRAM_IDS": "10099"})
        self.env.start()
        self.handlers = TelegramProBetaHandlers(settings())

    async def asyncTearDown(self) -> None:
        self.env.stop()

    async def test_feedback_rating_answers_callback_once(self) -> None:
        sid = uuid4()
        self.handlers._analytics = SimpleNamespace(add_rating=AsyncMock())
        query = SimpleNamespace(
            data=f"pro:fb:h:{sid}",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )

        await self.handlers.feedback_rating(callback_update(query), SimpleNamespace(user_data={}))

        self.assertEqual(query.answer.await_count, 1)
        self.assertEqual(query.answer.await_args.args[0], "Thanks — feedback saved.")

    async def test_feedback_rejection_answers_callback_once(self) -> None:
        sid = uuid4()
        query = SimpleNamespace(
            data=f"pro:fb:h:{sid}",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )

        await self.handlers.feedback_rating(
            callback_update(query, user_id=99999), SimpleNamespace(user_data={})
        )

        self.assertEqual(query.answer.await_count, 1)
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])

    async def test_feedback_text_start_answers_callback_once(self) -> None:
        sid = uuid4()
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(
            data=f"pro:fb:t:{sid}",
            answer=AsyncMock(),
            message=message,
        )
        ctx = SimpleNamespace(user_data={})

        await self.handlers.feedback_text_start(callback_update(query), ctx)

        self.assertEqual(query.answer.await_count, 1)
        self.assertEqual(ctx.user_data[PRO_FEEDBACK_KEY], str(sid))

    async def test_total_document_delivery_failure_refunds_and_records_failure(self) -> None:
        sid = uuid4()
        drawing = ProFile(filename="drawing.pdf", mime_type="application/pdf", data=b"%PDF-test")
        user_data = {
            PRO_STATE_KEY: {
                "project_information": "Synthetic QA Project",
                "site_notes": "Synthetic factual notes.",
                "standard_reference": "",
                "drawing": drawing,
                "template": None,
                PRO_SESSION_ID_KEY: str(sid),
            }
        }
        query = SimpleNamespace(
            data="pro:generate",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        bot = SimpleNamespace(
            send_chat_action=AsyncMock(),
            send_document=AsyncMock(side_effect=RuntimeError("telegram delivery unavailable")),
        )
        ctx = SimpleNamespace(user_data=user_data, bot=bot)
        self.handlers._service = SimpleNamespace(
            generate_professional_report=AsyncMock(return_value=report())
        )
        self.handlers._analytics = SimpleNamespace(record_generation=AsyncMock())

        with patch("bot.pro.telegram_beta_handlers.render_report_pdf", return_value=b"pdf"), patch(
            "bot.pro.telegram_beta_handlers.render_report_docx", return_value=b"docx"
        ):
            result = await self.handlers.generate(callback_update(query), ctx)

        self.assertEqual(result, -1)
        self.assertEqual(await self.handlers._limiter.current(str(TESTER_ID), date.today()), 0)
        analytics_call = self.handlers._analytics.record_generation.await_args
        self.assertEqual(analytics_call.kwargs["status"], "failed")
        self.assertFalse(analytics_call.kwargs["pdf_success"])
        self.assertFalse(analytics_call.kwargs["docx_success"])
        final_text = query.edit_message_text.await_args.args[0]
        self.assertIn("Telegram could not deliver", final_text)
        self.assertNotIn(PRO_STATE_KEY, ctx.user_data)


if __name__ == "__main__":
    unittest.main()
