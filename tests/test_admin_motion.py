from __future__ import annotations

import unittest

from bot.admin import _dashboard_html


class AdminDashboardMotionTests(unittest.TestCase):
    def test_dashboard_includes_motion_and_refresh_controls(self) -> None:
        html = _dashboard_html("csrf-test")

        self.assertIn('id="refresh"', html)
        self.assertIn('id="stamp"', html)
        self.assertIn('class="fill"', html)
        self.assertIn('@keyframes rise', html)
        self.assertIn('@keyframes grow', html)
        self.assertIn('@keyframes pulse', html)
        self.assertIn('prefers-reduced-motion:reduce', html)
        self.assertIn('Ticket updated', html)

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


if __name__ == "__main__":
    unittest.main()
