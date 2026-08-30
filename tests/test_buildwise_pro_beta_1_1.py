from __future__ import annotations

from datetime import date
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bot.config import Settings
from bot.pro.beta_analytics import ProBetaAnalyticsService
from bot.pro.beta_config import PRO_BETA_MAX_TESTERS, validated_pro_allowlist
from bot.pro.telegram_beta_handlers import PRO_FEEDBACK_KEY, TelegramProBetaHandlers


TESTER_ID = 10001
ADMIN_ID = 10099


def settings(*, allowed: str = str(TESTER_ID), daily_limit: int = 10) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="abcdefghijklmnop",
        BUILDWISE_PRO_ENABLED=True,
        BUILDWISE_PRO_WEB_ENABLED=False,
        BUILDWISE_PRO_ALLOWED_TELEGRAM_IDS=allowed,
        BUILDWISE_PRO_DAILY_REQUEST_LIMIT=daily_limit,
        BUILDWISE_PRO_MAX_FILE_SIZE_MB=1,
    )


def update(*, user_id: int = TESTER_ID, username="tester", first_name="Test", text=None, query=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(
            id=user_id, username=username, first_name=first_name
        ),
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
        callback_query=query,
    )


def context(*, user_data=None):
    return SimpleNamespace(user_data=user_data or {}, bot=SimpleNamespace())


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class ProBetaConfigurationTests(unittest.TestCase):
    def test_one_and_ten_testers_are_allowed(self) -> None:
        self.assertEqual(validated_pro_allowlist(settings(allowed="1")), frozenset({1}))
        ten = ",".join(str(i) for i in range(1, 11))
        self.assertEqual(len(validated_pro_allowlist(settings(allowed=ten))), PRO_BETA_MAX_TESTERS)

    def test_duplicates_are_deduplicated_before_counting(self) -> None:
        raw = ",".join([str(i) for i in range(1, 11)] + ["1", "2", "2"])
        self.assertEqual(len(validated_pro_allowlist(settings(allowed=raw))), 10)

    def test_eleven_unique_testers_fail(self) -> None:
        raw = ",".join(str(i) for i in range(1, 12))
        with self.assertRaisesRegex(ValueError, "at most 10 unique"):
            TelegramProBetaHandlers(settings(allowed=raw))

    def test_malformed_and_empty_segments_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "numeric Telegram IDs"):
            TelegramProBetaHandlers(settings(allowed="10001,nope"))
        with self.assertRaisesRegex(ValueError, "empty Telegram ID"):
            TelegramProBetaHandlers(settings(allowed="10001,,10002"))


class ProBetaTelegramCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.env = patch.dict(os.environ, {"BUILDWISE_ADMIN_TELEGRAM_IDS": str(ADMIN_ID)})
        self.env.start()
        self.handlers = TelegramProBetaHandlers(settings(daily_limit=10))
        self.handlers._analytics = SimpleNamespace(
            add_rating=AsyncMock(),
            add_text_feedback=AsyncMock(),
            summary=AsyncMock(
                return_value={
                    "testers_used": 1,
                    "reports_started": 3,
                    "reports_completed": 2,
                    "generation_failures": 1,
                    "average_generation_latency_ms": 1200,
                    "pdf_success_rate": 1.0,
                    "docx_success_rate": 0.5,
                    "helpful_count": 1,
                    "needs_improvement_count": 1,
                    "feedback_rate": 0.5,
                    "token_usage_available": False,
                    "cost_estimation_available": False,
                }
            ),
        )

    async def asyncTearDown(self) -> None:
        self.env.stop()

    async def test_myid_returns_numeric_id_username_and_handles_missing_username(self) -> None:
        u = update(user_id=123456789, username="waliu", first_name="Waliu")
        await self.handlers.myid(u, context())
        text = u.effective_message.reply_text.await_args.args[0]
        self.assertIn("Your Telegram ID: 123456789", text)
        self.assertIn("Username: @waliu", text)
        self.assertIn("First name: Waliu", text)

        u2 = update(user_id=22, username=None, first_name="NoUser")
        await self.handlers.myid(u2, context())
        text2 = u2.effective_message.reply_text.await_args.args[0]
        self.assertIn("Your Telegram ID: 22", text2)
        self.assertNotIn("Username:", text2)

    async def test_pro_status_allowlisted_and_non_allowlisted(self) -> None:
        await self.handlers._limiter.reserve(str(TESTER_ID), date.today(), 10)
        u = update()
        await self.handlers.pro_status(u, context())
        text = u.effective_message.reply_text.await_args.args[0]
        self.assertIn("access: Active", text)
        self.assertIn("Reports used today: 1 / 10", text)
        self.assertIn("PDF + DOCX", text)
        self.assertIn("not drawings, site notes, templates, or report bodies", text)

        denied = update(user_id=99999)
        await self.handlers.pro_status(denied, context())
        self.assertIn("limited private beta", denied.effective_message.reply_text.await_args.args[0])

    async def test_helpful_and_needs_improvement_feedback(self) -> None:
        for code, expected in [("h", "helpful"), ("n", "needs_improvement")]:
            sid = uuid4()
            q = SimpleNamespace(
                data=f"pro:fb:{code}:{sid}",
                answer=AsyncMock(),
                edit_message_reply_markup=AsyncMock(),
            )
            await self.handlers.feedback_rating(update(query=q), context())
            call = self.handlers._analytics.add_rating.await_args
            self.assertEqual(call.kwargs["session_id"], sid)
            self.assertEqual(call.kwargs["telegram_id"], TESTER_ID)
            self.assertEqual(call.kwargs["rating"], expected)

    async def test_text_feedback_is_stored_without_report_or_file_payload(self) -> None:
        sid = uuid4()
        ctx = context(user_data={PRO_FEEDBACK_KEY: str(sid)})
        u = update(text="The section order was easy to review.")
        result = await self.handlers.feedback_text_submit(u, ctx)
        self.assertEqual(result, -1)
        call = self.handlers._analytics.add_text_feedback.await_args
        self.assertEqual(
            set(call.kwargs), {"session_id", "telegram_id", "text"}
        )
        self.assertNotIn("drawing", call.kwargs)
        self.assertNotIn("report", call.kwargs)
        self.assertNotIn("template", call.kwargs)

    async def test_admin_stats_authorized_and_unauthorized(self) -> None:
        allowed = update(user_id=ADMIN_ID)
        await self.handlers.pro_beta_stats(allowed, context())
        text = allowed.effective_message.reply_text.await_args.args[0]
        self.assertIn("Allowlisted testers: 1 / 10", text)
        self.assertIn("Reports started: 3", text)
        self.assertIn("PDF success rate: 100.0%", text)
        self.assertIn("Approximate AI cost: unavailable", text)

        denied = update(user_id=555)
        await self.handlers.pro_beta_stats(denied, context())
        self.assertEqual(
            denied.effective_message.reply_text.await_args.args[0],
            "Admin authorization required.",
        )


class ProBetaAnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = ProBetaAnalyticsService(settings())
        self.service._request = AsyncMock()

    async def test_success_failure_latency_and_export_metadata_only(self) -> None:
        sid = uuid4()
        await self.service.record_generation(
            session_id=sid,
            status="success",
            latency_ms=1450,
            pdf_success=True,
            docx_success=False,
        )
        payload = self.service._request.await_args.kwargs["json"]
        self.assertEqual(payload["generation_status"], "success")
        self.assertEqual(payload["generation_latency_ms"], 1450)
        self.assertTrue(payload["pdf_success"])
        self.assertFalse(payload["docx_success"])
        forbidden = {"drawing", "drawing_text", "site_notes", "report", "report_body", "template", "project_document"}
        self.assertTrue(forbidden.isdisjoint(payload))

        await self.service.record_generation(
            session_id=sid, status="failed", latency_ms=800
        )
        failed = self.service._request.await_args.kwargs["json"]
        self.assertEqual(failed["generation_status"], "failed")
        self.assertEqual(failed["generation_latency_ms"], 800)

    async def test_feedback_payload_contains_no_report_or_upload_contents(self) -> None:
        sid = uuid4()
        self.service._assert_session_owner = AsyncMock()
        await self.service.add_rating(
            session_id=sid, telegram_id=TESTER_ID, rating="helpful"
        )
        rating_payload = self.service._request.await_args.kwargs["json"]
        self.assertEqual(set(rating_payload), {"session_id", "telegram_id", "rating"})

        await self.service.add_text_feedback(
            session_id=sid,
            telegram_id=TESTER_ID,
            text="Good workflow; no confidential project detail included.",
        )
        text_payload = self.service._request.await_args.kwargs["json"]
        self.assertEqual(
            set(text_payload), {"session_id", "telegram_id", "feedback_text"}
        )

    async def test_summary_calculates_beta_metrics(self) -> None:
        reports = [
            {"session_id": "1", "telegram_id": 10, "generation_status": "success", "generation_latency_ms": 1000, "pdf_success": True, "docx_success": True, "total_tokens": None},
            {"session_id": "2", "telegram_id": 11, "generation_status": "success", "generation_latency_ms": 2000, "pdf_success": False, "docx_success": True, "total_tokens": None},
            {"session_id": "3", "telegram_id": 10, "generation_status": "failed", "generation_latency_ms": 500, "pdf_success": None, "docx_success": None, "total_tokens": None},
        ]
        feedback = [
            {"session_id": "1", "telegram_id": 10, "rating": "helpful"},
            {"session_id": "2", "telegram_id": 11, "rating": "needs_improvement"},
        ]
        self.service._request.side_effect = [FakeResponse(reports), FakeResponse(feedback)]
        summary = await self.service.summary()
        self.assertEqual(summary["testers_used"], 2)
        self.assertEqual(summary["reports_started"], 3)
        self.assertEqual(summary["reports_completed"], 2)
        self.assertEqual(summary["generation_failures"], 1)
        self.assertEqual(summary["average_generation_latency_ms"], 1167)
        self.assertEqual(summary["pdf_success_rate"], 0.5)
        self.assertEqual(summary["docx_success_rate"], 1.0)
        self.assertEqual(summary["feedback_rate"], 1.0)
        self.assertFalse(summary["token_usage_available"])
        self.assertFalse(summary["cost_estimation_available"])


if __name__ == "__main__":
    unittest.main()
