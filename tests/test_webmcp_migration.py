from pathlib import Path
import unittest


class WebMCPMigrationTests(unittest.TestCase):
    def test_usage_tables_are_rls_and_service_role_only(self) -> None:
        sql = Path("migrations/005_webmcp_usage.sql").read_text()
        self.assertIn("alter table public.webmcp_usage_daily enable row level security", sql)
        self.assertIn("alter table public.webmcp_usage_reservations enable row level security", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)

    def test_usage_reservations_support_exact_refunds(self) -> None:
        sql = Path("migrations/005_webmcp_usage.sql").read_text()
        self.assertIn("create or replace function public.consume_webmcp_usage", sql)
        self.assertIn("create or replace function public.finalize_webmcp_usage", sql)
        self.assertIn("create or replace function public.refund_webmcp_usage", sql)
        self.assertIn("and r.state = 'reserved'", sql)
        self.assertIn("greatest(0, u.ai_requests - 1)", sql)


if __name__ == "__main__":
    unittest.main()
