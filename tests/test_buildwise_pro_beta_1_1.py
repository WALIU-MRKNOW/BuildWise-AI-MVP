from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pydantic import ValidationError
from telegram.ext import ConversationHandler

from bot.config import Settings
from bot.pro.telegram_beta_handlers import (
    FEEDBACK_SESSION_KEY,
    FEEDBACK_TEXT,
    TelegramProBetaHandlers,
)


AUTHORIZED_ID = 6901754599
OTHER_ID = 9543030600
SESSION_ID = "12345678-1234-4234-9234-123456789abc"


def settings(*, allowed: str = str(AUTHORIZED_ID), admin_ids: str = str(AUTHORIZED_ID)) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        ADMIN_USERNAME="buildwise-admin",
        ADMIN_PASSWORD="this-is-a-test-admin-password",
        BUILDWISE_ADMIN_TELEGRAM_IDS=admin_ids,
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=allowed,
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=10,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=10,
    )


def message(text: str | None = None):
    return SimpleNamespace(text=text, reply_text=AsyncMock())


def callback(data: str):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


def update(*, msg=None, query=None, user_id: int = AUTHORIZED_ID, username="waliu344", first_name="WALIU"):
    return SimpleNamespace(
        effective_message=msg,
        effective_user=SimpleNamespace(
            id=user_id,
            username=username,
            first_name=first_name,
            full_name=first_name,
        ),
        effective_chat=SimpleNamespace(id=999),
        callback_query=query,
    )


def context(*, user_data=None):
    return SimpleNamespace(user_data=user_data or {})


class ProBetaHardCapTests(unittest.TestCase):
    def test_one_tester(self) -> None:
        self.assertEqual(settings(allowed="1").buildwise_pro_allowed_telegram_ids, frozenset({1}))

    def test_ten_testers(self) -> None:
        raw = ",".join(str(i) for i in range(1, 11))
        self.assertEqual(len(settings(allowed=raw).buildwise_pro_allowed_telegram_ids), 10)

    def test_duplicate_ids_are_deduplicated_before_counting(self) -> None:
        raw = "1,2,3,4,5,6,7,8,9,10,10,1"
        configured = settings(allowed=raw)
        self.assertEqual(len(configured.buildwise_pro_allowed_telegram_ids), 10)

    def test_eleven_unique_testers_fail_validation(self) -> None:
        raw = ",".join(str(i) for i in range(1, 12))
        with self.assertRaises(ValidationError):
            settings(allowed=raw)

    def test_malformed_telegram_ids_fail_validation(self) -> None:
        with self.assertRaises(ValidationError):
            settings(allowed="1,not-an-id")
        with self.assertRaises(ValidationError):
            settings(allowed="1,,2")
        with self.assertRaises(ValidationError):
            settings(allowed="1,-2")


class ProBetaCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.handlers = TelegramProBetaHandlers(settings())
        self.handlers._analytics = SimpleNamespace(
            start_session=AsyncMock(return_value=SESSION_ID),
            session_belongs_to=AsyncMock(return_value=True),
            record_rating=AsyncMock(),
            record_comment=AsyncMock(),
            record_generation=AsyncMock(),
            stats=AsyncMock(
                return_value={
                    "allowlisted_testers": 2,
                    "testers_used": 2,
                    "reports_started": 5,
                    "reports_completed": 4,
                    "generation_failures": 1,
                    "average_latency_ms": 1200,
                    "pdf_success_rate": 100.0,
                    "docx_success_rate": 75.0,
                    "helpful_count": 2,
                    "needs_improvement_count": 1,
                    "feedback_rate": 75.0,
                    "token_usage_available": False,
                    "cost_estimation_available": False,
                }
            ),
        )

    async def test_myid_returns_numeric_id_username_and_first_name(self) -> None:
        msg = message()
        await self.handlers.myid(update(msg=msg), context())
        text = msg.reply_text.await_args.args[0]
        self.assertIn(str(AUTHORIZED_ID), text)
        self.assertIn("@waliu344", text)
        self.assertIn("WALIU", text)

    async def test_myid_handles_missing_username(self) -> None:
        msg = message()
        await self.handlers.myid(update(msg=msg, username=None), context())
        text = msg.reply_text.await_args.args[0]
        self.assertIn(str(AUTHORIZED_ID), text)
        self.assertNotIn("Username:", text)

    async def test_pro_status_allowlisted_and_daily_usage(self) -> None:
        self.handlers._limiter.current = AsyncMock(return_value=3)
        msg = message()
        await self.handlers.pro_status(update(msg=msg), context())
        text = msg.reply_text.await_args.args[0]
        self.assertIn("access: Active", text)
        self.assertIn("3 / 10", text)
        self.assertIn("PDF + DOCX", text)

    async def test_pro_status_non_allowlisted(self) -> None:
        msg = message()
        await self.handlers.pro_status(update(msg=msg, user_id=OTHER_ID), context())
        self.assertIn("limited 10-person beta", msg.reply_text.await_args.args[0])

    async def test_helpful_rating_stores_only_safe_reference_metadata(self) -> None:
        q = callback(f"pro:fb:up:{SESSION_ID}")
        await self.handlers.feedback_rating(update(query=q), context())
        kwargs = self.handlers._analytics.record_rating.await_args.kwargs
        self.assertEqual(kwargs, {
            "session_id": SESSION_ID,
            "telegram_id": AUTHORIZED_ID,
            "rating": "helpful",
        })
        self.assertNotIn("report", kwargs)
        self.assertNotIn("drawing", kwargs)

    async def test_needs_improvement_rating(self) -> None:
        q = callback(f"pro:fb:down:{SESSION_ID}")
        await self.handlers.feedback_rating(update(query=q), context())
        self.assertEqual(
            self.handlers._analytics.record_rating.await_args.kwargs["rating"],
            "needs_improvement",
        )

    async def test_text_feedback_flow_and_privacy_warning(self) -> None:
        q = callback(f"pro:fb:text:{SESSION_ID}")
        ctx = context()
        result = await self.handlers.feedback_comment_prompt(update(query=q), ctx)
        self.assertEqual(result, FEEDBACK_TEXT)
        self.assertEqual(ctx.user_data[FEEDBACK_SESSION_KEY], SESSION_ID)
        warning = q.message.reply_text.await_args.args[0]
        self.assertIn("Do not include confidential", warning)

        msg = message("The steps were clear and the output format was useful.")
        result2 = await self.handlers.feedback_comment(update(msg=msg), ctx)
        self.assertEqual(result2, ConversationHandler.END)
        kwargs = self.handlers._analytics.record_comment.await_args.kwargs
        self.assertEqual(kwargs["session_id"], SESSION_ID)
        self.assertEqual(kwargs["telegram_id"], AUTHORIZED_ID)
        self.assertEqual(kwargs["feedback_text"], msg.text)
        self.assertNotIn("drawing", kwargs)
        self.assertNotIn("site_notes", kwargs)
        self.assertNotIn("report_body", kwargs)
        self.assertNotIn("file", kwargs)

    async def test_authorized_pro_beta_stats(self) -> None:
        msg = message()
        await self.handlers.pro_beta_stats(update(msg=msg), context())
        text = msg.reply_text.await_args.args[0]
        self.assertIn("Allowlisted testers: 2 / 10", text)
        self.assertIn("Reports started: 5", text)
        self.assertIn("PDF success rate: 100.0%", text)
        self.assertIn("AI cost estimate: unavailable", text)

    async def test_unauthorized_pro_beta_stats_denied(self) -> None:
        msg = message()
        await self.handlers.pro_beta_stats(update(msg=msg, user_id=OTHER_ID), context())
        self.assertIn("restricted to an authorized BuildWise admin", msg.reply_text.await_args.args[0])
        self.handlers._analytics.stats.assert_not_awaited()

    async def test_generation_metadata_method_records_success_failure_latency_and_exports(self) -> None:
        await self.handlers._record_generation_safely(
            session_id=SESSION_ID,
            telegram_id=AUTHORIZED_ID,
            success=True,
            latency_ms=4321,
            pdf_success=True,
            docx_success=False,
        )
        kwargs = self.handlers._analytics.record_generation.await_args.kwargs
        self.assertTrue(kwargs["success"])
        self.assertEqual(kwargs["latency_ms"], 4321)
        self.assertTrue(kwargs["pdf_success"])
        self.assertFalse(kwargs["docx_success"])

        await self.handlers._record_generation_safely(
            session_id=SESSION_ID,
            telegram_id=AUTHORIZED_ID,
            success=False,
            latency_ms=500,
            pdf_success=None,
            docx_success=None,
        )
        self.assertFalse(self.handlers._analytics.record_generation.await_args.kwargs["success"])


if __name__ == "__main__":
    unittest.main()
