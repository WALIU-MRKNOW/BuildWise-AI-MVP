from __future__ import annotations

import unittest
from pathlib import Path


class StudentOnboardingMigrationTests(unittest.TestCase):
    def test_migration_adds_profile_fields_and_private_activity_table(self) -> None:
        sql = Path("migrations/006_student_onboarding_activity.sql").read_text(
            encoding="utf-8"
        ).lower()

        self.assertIn("student_name text", sql)
        self.assertIn("university text", sql)
        self.assertIn("onboarding_completed boolean", sql)
        self.assertIn("create table if not exists public.user_activity_events", sql)
        self.assertIn("alter table public.user_activity_events enable row level security", sql)
        self.assertNotIn("create policy", sql)

    def test_existing_users_are_grandfathered(self) -> None:
        sql = Path("migrations/006_student_onboarding_activity.sql").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn("onboarding_completed = true", sql)
        self.assertIn("student_name = coalesce", sql)


if __name__ == "__main__":
    unittest.main()
