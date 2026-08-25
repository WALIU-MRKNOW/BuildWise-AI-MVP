from pathlib import Path
import unittest


class ReferralRpcMigrationTests(unittest.TestCase):
    def test_referral_update_qualifies_referred_by_column(self) -> None:
        sql = Path("migrations/004_fix_referral_rpc_ambiguity.sql").read_text()
        self.assertIn("update public.users as u", sql)
        self.assertIn("and u.referred_by is null", sql)
        self.assertNotIn("and referred_by is null", sql)

    def test_service_role_only_rpc_permissions_remain(self) -> None:
        sql = Path("migrations/004_fix_referral_rpc_ambiguity.sql").read_text()
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)


if __name__ == "__main__":
    unittest.main()
