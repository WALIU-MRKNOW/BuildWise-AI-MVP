import re
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from bot.api import RuntimeHealth, create_api
from bot.config import Settings
from bot.pro.models import PROFESSIONAL_REVIEW_NOTICE, ProfessionalReport, TemplateSection


def settings(*, enabled: bool) -> Settings:
    values = dict(
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
        BUILDWISE_PRO_ENABLED=enabled,
    )
    if enabled:
        values.update(
            BUILDWISE_PRO_ACCESS_KEY="pro-preview-access-key-123456789",
            BUILDWISE_PRO_SESSION_SECRET="pro-session-secret-abcdefghijklmnopqrstuvwxyz-123456",
            BUILDWISE_PRO_DAILY_REQUEST_LIMIT=2,
        )
    return Settings(**values)


def sample_report(*, template: bool = False) -> ProfessionalReport:
    return ProfessionalReport(
        project_document_information=["Project A"],
        summary="Draft summary.",
        observations=["Observation from supplied material."],
        drawing_sketch_findings=["Drawing finding."],
        site_note_findings=["Site finding."],
        relevant_technical_considerations=["Verify on site."],
        referenced_code_standard=["No clause compliance verified."],
        recommendations=["Professional review required."],
        limitations_assumptions=["No site inspection by BuildWise."],
        uncertainties_missing_information=["Load data not supplied."],
        template_structure_used=template,
        template_sections=(
            [
                TemplateSection(
                    heading="Company Findings",
                    content=["Mapped finding from supplied evidence."],
                )
            ]
            if template
            else []
        ),
    )


class BuildWiseProRouterTests(unittest.TestCase):
    def _authenticated_client(self):
        app = create_api(RuntimeHealth(ready=True), settings(enabled=True))
        client = TestClient(app, base_url="https://testserver")
        client.post(
            "/pro/login",
            data={"access_key": "pro-preview-access-key-123456789"},
        )
        page = client.get("/pro")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        return client, csrf

    def test_disabled_feature_does_not_mount_pro_routes(self) -> None:
        app = create_api(RuntimeHealth(ready=True), settings(enabled=False))
        paths = {getattr(route, "path", None) for route in app.routes}
        self.assertNotIn("/pro", paths)

    def test_login_cookie_security_and_csrf(self) -> None:
        app = create_api(RuntimeHealth(ready=True), settings(enabled=True))
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/pro/login",
            data={"access_key": "pro-preview-access-key-123456789"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=strict", cookie)

        page = client.get("/pro")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("pro-preview-access-key-123456789", page.text)
        match = re.search(r'name="csrf" value="([^"]+)"', page.text)
        self.assertIsNotNone(match)
        csrf = match.group(1)

        invalid = client.post(
            "/pro/report",
            data={"csrf": "wrong", "site_notes": "Observed cracking."},
            files={"drawing": ("drawing.pdf", b"%PDF-1.7 test", "application/pdf")},
        )
        self.assertEqual(invalid.status_code, 403)

        invalid_file = client.post(
            "/pro/report",
            data={"csrf": csrf, "site_notes": "Observed cracking."},
            files={"drawing": ("drawing.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(invalid_file.status_code, 400)

    def test_structured_report_renders_with_safety_notice(self) -> None:
        client, csrf = self._authenticated_client()
        with patch(
            "bot.pro.router.ProfessionalReportService.generate_professional_report",
            new=AsyncMock(return_value=sample_report()),
        ):
            response = client.post(
                "/pro/report",
                data={
                    "csrf": csrf,
                    "project_information": "Project A",
                    "site_notes": "Observed cracking near support.",
                    "standard_reference": "AS 3600",
                },
                files={"drawing": ("drawing.pdf", b"%PDF-1.7 test", "application/pdf")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("DRAFT — PROFESSIONAL REVIEW REQUIRED", response.text)
        self.assertIn(PROFESSIONAL_REVIEW_NOTICE, response.text)
        self.assertIn("Load data not supplied", response.text)
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_markdown_company_template_is_validated_and_rendered_in_template_order(self) -> None:
        client, csrf = self._authenticated_client()
        mock_generate = AsyncMock(return_value=sample_report(template=True))
        with patch(
            "bot.pro.router.ProfessionalReportService.generate_professional_report",
            new=mock_generate,
        ):
            response = client.post(
                "/pro/report",
                data={
                    "csrf": csrf,
                    "project_information": "Project A",
                    "site_notes": "Observed cracking near support.",
                },
                files={
                    "drawing": ("drawing.pdf", b"%PDF-1.7 test", "application/pdf"),
                    "template": (
                        "company-template.md",
                        b"# Company Report\n## Findings",
                        "text/markdown",
                    ),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Company Findings", response.text)
        self.assertIn("Mapped finding from supplied evidence", response.text)
        call = mock_generate.await_args.kwargs
        self.assertEqual(call["template"].mime_type, "text/markdown")

    def test_unauthorized_report_request_fails(self) -> None:
        app = create_api(RuntimeHealth(ready=True), settings(enabled=True))
        client = TestClient(app, base_url="https://testserver")
        response = client.post(
            "/pro/report",
            data={"csrf": "none", "site_notes": "Observed cracking."},
            files={"drawing": ("drawing.pdf", b"%PDF-1.7 test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
