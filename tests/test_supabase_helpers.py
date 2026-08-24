import unittest
from datetime import datetime, timezone

from bot.services.supabase_service import local_date, validate_timezone


class SupabaseHelperTests(unittest.TestCase):
    def test_timezone_validation(self) -> None:
        self.assertEqual(validate_timezone("Africa/Lagos"), "Africa/Lagos")

    def test_invalid_timezone_has_friendly_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Area/City|timezone"):
            validate_timezone("Not/A-Timezone")

    def test_local_date_uses_user_timezone(self) -> None:
        moment = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(str(local_date("America/Chicago", moment)), "2025-12-31")


if __name__ == "__main__":
    unittest.main()
