from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReferralRetirementTests(unittest.TestCase):
    def test_runtime_no_longer_registers_referrals(self) -> None:
        app_source = (ROOT / "bot" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("ReferralHandlers", app_source)
        self.assertNotIn("ReferralService", app_source)
        self.assertFalse((ROOT / "bot" / "handlers" / "referral_handlers.py").exists())
        self.assertFalse((ROOT / "bot" / "services" / "referral_service.py").exists())

    def test_cleanup_migration_removes_referral_objects(self) -> None:
        migration = (ROOT / "migrations" / "006_remove_referral_system.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("drop table if exists public.referral_rewards", migration)
        self.assertIn("drop table if exists public.referral_credit_balances", migration)
        self.assertIn("drop table if exists public.usage_reservations", migration)
        self.assertIn("drop column if exists referred_by", migration)
        self.assertIn("drop column if exists referral_code", migration)


if __name__ == "__main__":
    unittest.main()
