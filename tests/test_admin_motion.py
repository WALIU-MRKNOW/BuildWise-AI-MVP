from __future__ import annotations

import unittest

from bot.admin import _dashboard_html


class AdminDashboardMotionTests(unittest.TestCase):
    def test_dashboard_includes_motion_and_refresh_controls(self) -> None:
        html = _dashboard_html("csrf-test")

        self.assertIn('id="refresh"', html)
        self.assertIn('id="stamp"', html)
        self.assertIn('animateNumber', html)
        self.assertIn('class="barfill"', html)
        self.assertIn('@keyframes rise', html)
        self.assertIn('@keyframes grow', html)
        self.assertIn('prefers-reduced-motion:reduce', html)
        self.assertIn('Ticket status updated', html)

    def test_dashboard_keeps_private_api_paths_and_csrf(self) -> None:
        html = _dashboard_html("csrf-test")

        self.assertIn("/admin/api/dashboard", html)
        self.assertIn("/admin/api/tickets/", html)
        self.assertIn("X-BuildWise-CSRF", html)
        self.assertIn("csrf-test", html)

    def test_dashboard_contains_no_referral_ui(self) -> None:
        html = _dashboard_html("csrf-test").lower()

        self.assertNotIn("referral", html)
        self.assertNotIn("referrer", html)
        self.assertNotIn("reward credits", html)
        self.assertNotIn("qualified referrals", html)

    def test_dashboard_exposes_searchable_user_ids_and_richer_activity(self) -> None:
        html = _dashboard_html("csrf-test")

        self.assertIn("All users", html)
        self.assertIn("Telegram ID", html)
        self.assertIn("Institution", html)
        self.assertIn('id="userSearch"', html)
        self.assertIn('id="userPrev"', html)
        self.assertIn('id="userNext"', html)
        self.assertIn('id="activitySearch"', html)
        self.assertIn('id="activityMode"', html)
        self.assertIn("u.telegram_id", html)
        self.assertIn("a.telegram_id", html)
        self.assertIn("u.activity_30d", html)
        self.assertIn("a.category", html)


if __name__ == "__main__":
    unittest.main()
