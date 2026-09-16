import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from bot.domain import Mode
from bot.handlers.repository_audit_handlers import (
    PENDING_UPLOAD_TTL_SECONDS,
    SKETCH_REPORT_TTL_SECONDS,
    RepositoryAuditHandlers,
    visible_admin_navigation_ids,
)
from bot.pro.auth import PreviewUsageLimiter
from bot.router import RequestRouter
from bot.services.reward_usage_service import StudentAccessSupabaseService


class RouterRegressionTests(unittest.TestCase):
    def test_bare_construction_terms_route_to_study_without_image(self) -> None:
        router = RequestRouter()
        for term in ("reinforcement", "foundation", "concrete", "drainage", "beam"):
            with self.subTest(term=term):
                decision = router.route(term)
                self.assertEqual(decision.mode, Mode.STUDY)
                self.assertEqual(decision.reason, "text-only defaults to study")

    def test_building_photo_language_still_routes_to_building(self) -> None:
        decision = RequestRouter().route("concrete crack", has_image=True)
        self.assertEqual(decision.mode, Mode.BUILDING)


class EphemeralCacheRegressionTests(unittest.TestCase):
    def test_stale_sketch_reports_are_removed(self) -> None:
        user_data = {
            "sketch_reports": {
                "old": {"created_at_monotonic": 0.0, "image": b"old"},
                "fresh": {
                    "created_at_monotonic": 200.0,
                    "image": b"fresh",
                },
            }
        }
        RepositoryAuditHandlers._prune_sketch_reports(
            user_data, now=float(SKETCH_REPORT_TTL_SECONDS)
        )
        self.assertNotIn("old", user_data["sketch_reports"])
        self.assertIn("fresh", user_data["sketch_reports"])

    def test_abandoned_pending_upload_expires(self) -> None:
        user_data = {
            "pending_request": {
                "created_at_monotonic": 0.0,
                "image_bytes": b"private-image",
            }
        }
        RepositoryAuditHandlers._prune_pending_upload(
            user_data, now=float(PENDING_UPLOAD_TTL_SECONDS)
        )
        self.assertNotIn("pending_request", user_data)


class AdminNavigationRegressionTests(unittest.TestCase):
    def test_beta_stats_navigation_hidden_while_pro_paused(self) -> None:
        settings = SimpleNamespace(
            buildwise_pro_enabled=False,
            admin_username="admin",
            admin_password=SecretStr("a" * 20),
        )
        self.assertEqual(
            visible_admin_navigation_ids(settings, frozenset({12345})),
            frozenset(),
        )

    def test_beta_stats_navigation_hidden_when_admin_credentials_invalid(self) -> None:
        settings = SimpleNamespace(
            buildwise_pro_enabled=True,
            admin_username="",
            admin_password=None,
        )
        self.assertEqual(
            visible_admin_navigation_ids(settings, frozenset({12345})),
            frozenset(),
        )

    def test_beta_stats_navigation_kept_when_both_surfaces_are_configured(self) -> None:
        settings = SimpleNamespace(
            buildwise_pro_enabled=True,
            admin_username="admin",
            admin_password=SecretStr("a" * 20),
        )
        self.assertEqual(
            visible_admin_navigation_ids(settings, frozenset({12345})),
            frozenset({12345}),
        )


class ProConfigurationRegressionTests(unittest.TestCase):
    def test_pro_usage_limiter_accepts_legacy_supabase_key_alias(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BUILDWISE_PRO_ENABLED": "true",
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SECRET_KEY": "",
                "SUPABASE_SERVICE_ROLE_KEY": "legacy-service-role-key",
            },
            clear=False,
        ):
            limiter = PreviewUsageLimiter()
        self.assertTrue(limiter._persistent)
        self.assertEqual(limiter._key, "legacy-service-role-key")


class FakeStudentAccessService(StudentAccessSupabaseService):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, object, dict | None]] = []
        self.deleted_files: list[str] = []
        self.conversations = [
            {"id": f"c-{index}", "file_reference": None}
            for index in range(501)
        ]
        self.conversations[0]["file_reference"] = "files/first.pdf"
        self.conversations[-1]["file_reference"] = "files/last.pdf"
        self.saved_items = [
            {"id": "s-1", "file_reference": "files/saved.pdf"},
            {"id": "s-2", "file_reference": "files/first.pdf"},
        ]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json=None,
        content=None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ):
        del content, allow_not_found
        self.calls.append((method, path, params, json, headers))
        if method == "GET" and path == "/rest/v1/conversations":
            offset = int((params or {}).get("offset", "0"))
            limit = int((params or {}).get("limit", "500"))
            return self.conversations[offset : offset + limit]
        if method == "GET" and path == "/rest/v1/saved_items":
            offset = int((params or {}).get("offset", "0"))
            limit = int((params or {}).get("limit", "500"))
            return self.saved_items[offset : offset + limit]
        if method == "POST" and path == "/rest/v1/saved_items":
            return [{"id": "new-profile-id"}]
        if method == "PATCH" and path == "/rest/v1/conversations":
            return [{"id": "conversation-id"}]
        return None

    async def delete_file(self, object_path: str) -> None:
        self.deleted_files.append(object_path)


class StudentDataLifecycleRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_history_pages_all_conversations_and_saved_file_refs(self) -> None:
        service = FakeStudentAccessService()
        count = await service.delete_history("user-1")

        self.assertEqual(count, 501)
        self.assertEqual(
            set(service.deleted_files),
            {"files/first.pdf", "files/last.pdf", "files/saved.pdf"},
        )
        conversation_gets = [
            call
            for call in service.calls
            if call[0] == "GET" and call[1] == "/rest/v1/conversations"
        ]
        self.assertEqual(len(conversation_gets), 2)
        self.assertEqual(conversation_gets[0][2]["offset"], "0")
        self.assertEqual(conversation_gets[1][2]["offset"], "500")

        saved_delete = next(
            call
            for call in service.calls
            if call[0] == "DELETE" and call[1] == "/rest/v1/saved_items"
        )
        self.assertEqual(saved_delete[2]["item_type"], "neq.student_profile")

    async def test_identity_insert_happens_before_old_profile_cleanup(self) -> None:
        service = FakeStudentAccessService()
        await service.save_student_identity(
            "user-1", name="Student", university="BuildWise University"
        )

        self.assertEqual(service.calls[0][0:2], ("POST", "/rest/v1/saved_items"))
        self.assertEqual(service.calls[1][0:2], ("DELETE", "/rest/v1/saved_items"))
        self.assertEqual(service.calls[1][2]["id"], "neq.new-profile-id")

    async def test_attachment_patch_is_bound_to_user_and_conversation(self) -> None:
        service = FakeStudentAccessService()
        await service.attach_file_to_conversation(
            user_id="user-1",
            conversation_id="conversation-id",
            file_reference="files/success.pdf",
        )
        patch_call = service.calls[-1]
        self.assertEqual(patch_call[0:2], ("PATCH", "/rest/v1/conversations"))
        self.assertEqual(patch_call[2]["id"], "eq.conversation-id")
        self.assertEqual(patch_call[2]["user_id"], "eq.user-1")
        self.assertEqual(patch_call[3], {"file_reference": "files/success.pdf"})


class TelegramUploadRegressionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _handler(*, max_bytes: int, download: bytes) -> RepositoryAuditHandlers:
        handler = object.__new__(RepositoryAuditHandlers)
        handler._settings = SimpleNamespace(
            max_file_size_bytes=max_bytes,
            max_file_size_mb=1,
        )
        handler._router = RequestRouter()
        handler._profile = AsyncMock(return_value=SimpleNamespace())
        handler._download_file = AsyncMock(return_value=download)
        handler._database = SimpleNamespace(upload_file=AsyncMock())
        return handler

    async def test_downloaded_size_is_checked_when_telegram_size_is_missing(self) -> None:
        handler = self._handler(max_bytes=3, download=b"1234")
        message = SimpleNamespace(
            text=None,
            caption=None,
            photo=None,
            document=SimpleNamespace(
                file_size=None,
                mime_type="application/pdf",
                file_name="large.pdf",
                file_id="file-id",
            ),
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})

        await handler.handle_message(update, context)

        self.assertTrue(message.reply_text.await_count)
        self.assertIn("too large", message.reply_text.await_args.args[0].lower())

    async def test_unlabelled_image_is_not_uploaded_before_clarification(self) -> None:
        handler = self._handler(max_bytes=100, download=b"image-bytes")
        photo = SimpleNamespace(file_size=None, file_id="photo-id")
        message = SimpleNamespace(
            text=None,
            caption=None,
            photo=[photo],
            document=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})

        await handler.handle_message(update, context)

        self.assertEqual(handler._database.upload_file.await_count, 0)
        self.assertEqual(
            context.user_data["pending_request"]["image_bytes"],
            b"image-bytes",
        )
        self.assertIn("created_at_monotonic", context.user_data["pending_request"])


if __name__ == "__main__":
    unittest.main()
