from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.services.admin_service import AdminService


def test_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


class AdminVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_keeps_all_user_identity_fields_separate(self) -> None:
        service = AdminService(test_settings())

        users = [
            {
                "id": "user-1",
                "telegram_id": 111,
                "username": "alice",
                "display_name": "Alice Telegram",
                "created_at": "2026-09-01T10:00:00+00:00",
                "updated_at": "2026-09-08T10:00:00+00:00",
            },
            {
                "id": "user-2",
                "telegram_id": 222,
                "username": None,
                "display_name": "Bob Telegram",
                "created_at": "2026-09-02T10:00:00+00:00",
                "updated_at": "2026-09-08T11:00:00+00:00",
            },
        ]
        profiles = [
            {
                "user_id": "user-1",
                "content": json.dumps(
                    {
                        "name": "Alice Example",
                        "university": "Yaba College of Technology",
                    }
                ),
                "created_at": "2026-09-01T10:01:00+00:00",
            }
        ]
        recent = [
            {
                "id": "conversation-1",
                "user_id": "user-1",
                "mode": "study",
                "category": "beam reactions",
                "message": "Calculate the beam reactions.",
                "created_at": "2026-09-08T12:30:00+00:00",
            }
        ]
        mode_rows = [
            {
                "mode": "study",
                "user_id": "user-1",
                "created_at": "2026-09-08T12:30:00+00:00",
            },
            {
                "mode": "study",
                "user_id": "user-1",
                "created_at": "2026-09-09T09:15:00+00:00",
            },
            {
                "mode": "sketch",
                "user_id": "user-2",
                "created_at": "2026-09-08T11:00:00+00:00",
            },
        ]

        async def fake_rows(path: str, *, params=None):
            params = params or {}
            if path == "/rest/v1/users":
                return users
            if path == "/rest/v1/saved_items":
                return profiles
            if path == "/rest/v1/usage_daily":
                return [{"request_count": 12}, {"request_count": 2}]
            if path == "/rest/v1/support_tickets":
                return []
            if path == "/rest/v1/conversations":
                selected = params.get("select")
                if selected == "id,user_id,mode,category,message,created_at":
                    return recent
                if selected == "mode,user_id,created_at":
                    return mode_rows
                if selected == "user_id":
                    return [{"user_id": "user-1"}, {"user_id": "user-2"}]
            return []

        service._rows = AsyncMock(side_effect=fake_rows)  # type: ignore[method-assign]
        payload = await service.dashboard()

        self.assertEqual(len(payload["users"]), 2)
        alice = payload["users"][0]
        self.assertEqual(alice["display_name"], "Alice Example")
        self.assertEqual(alice["username"], "alice")
        self.assertEqual(alice["telegram_id"], 111)
        self.assertEqual(alice["university"], "Yaba College of Technology")
        self.assertEqual(alice["activity_30d"], 2)
        self.assertEqual(alice["last_activity_30d"], "2026-09-09T09:15:00+00:00")

        bob = payload["users"][1]
        self.assertEqual(bob["display_name"], "Bob Telegram")
        self.assertIsNone(bob["username"])
        self.assertEqual(bob["telegram_id"], 222)
        self.assertEqual(bob["university"], "Institution not added")

        activity = payload["recent_activity"][0]
        self.assertEqual(activity["display_name"], "Alice Example")
        self.assertEqual(activity["username"], "alice")
        self.assertEqual(activity["telegram_id"], 111)
        self.assertEqual(activity["university"], "Yaba College of Technology")
        self.assertEqual(activity["category"], "beam reactions")
        self.assertEqual(activity["message"], "Calculate the beam reactions.")

        self.assertEqual(payload["summary"]["users"], 2)
        self.assertEqual(payload["summary"]["active_users_7d"], 2)
        self.assertEqual(payload["summary"]["requests_today"], 14)


if __name__ == "__main__":
    unittest.main()
