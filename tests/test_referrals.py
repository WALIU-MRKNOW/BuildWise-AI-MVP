import unittest
from pathlib import Path

from bot.services.referral_service import build_referral_link, normalize_referral_code


VALID_CODE = "0123456789abcdef0123456789abcdef"


class ReferralHelperTests(unittest.TestCase):
    def test_normalize_accepts_uuid_hex_payload(self) -> None:
        self.assertEqual(normalize_referral_code(VALID_CODE.upper()), VALID_CODE)

    def test_normalize_rejects_invalid_or_oversized_payloads(self) -> None:
        self.assertIsNone(normalize_referral_code("not-a-referral"))
        self.assertIsNone(normalize_referral_code(VALID_CODE + "00"))
        self.assertIsNone(normalize_referral_code(None))

    def test_deep_link_uses_telegram_start_parameter(self) -> None:
        self.assertEqual(
            build_referral_link("@BuildWiseBot", VALID_CODE),
            f"https://t.me/BuildWiseBot?start={VALID_CODE}",
        )

    def test_deep_link_rejects_invalid_code(self) -> None:
        with self.assertRaises(ValueError):
            build_referral_link("BuildWiseBot", "bad")


class ReferralMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path("migrations/002_referral_tracking.sql").read_text(encoding="utf-8")

    def test_referral_code_is_unique_and_referred_by_is_indexed(self) -> None:
        self.assertIn("users_referral_code_key", self.sql)
        self.assertIn("users_referred_by_idx", self.sql)

    def test_attribution_only_runs_for_new_users(self) -> None:
        self.assertIn("if v_was_new and p_referral_code is not null", self.sql)

    def test_self_referrals_are_rejected(self) -> None:
        self.assertIn("v_referrer_id <> v_user.id", self.sql)

    def test_referral_rpc_is_service_role_only(self) -> None:
        signature = "public.register_referral_start(bigint, text, text, text)"
        self.assertIn(f"revoke all on function {signature} from public, anon, authenticated", self.sql)
        self.assertIn(f"grant execute on function {signature} to service_role", self.sql)

    def test_rls_remains_enabled(self) -> None:
        self.assertIn("alter table public.users enable row level security", self.sql)


if __name__ == "__main__":
    unittest.main()
