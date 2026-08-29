"""Feature-flagged BuildWise Pro web surface for draft professional reports."""

from __future__ import annotations

from datetime import date
from html import escape
import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from bot.config import Settings
from bot.pro.ai_service import ProFile, ProfessionalReportService
from bot.pro.auth import (
    SESSION_COOKIE,
    PreviewUsageLimiter,
    access_key_matches,
    csrf_matches,
    csrf_token,
    issue_session,
    stable_subject_hash,
    validate_pro_configuration,
    validate_session,
)
from bot.pro.models import PROFESSIONAL_REVIEW_NOTICE, ProfessionalReport, ProfessionalReportRequest
from bot.services.openai_service import AIServiceError

logger = logging.getLogger(__name__)

_ALLOWED_DRAWING_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _secure_html(content: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _page(body: str, *, title: str = "BuildWise Pro") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#171717;background:#faf9f6}}
.card{{background:white;border:1px solid #ddd;border-radius:16px;padding:24px;margin:18px 0}}
label{{display:block;font-weight:700;margin-top:14px}} input,textarea{{width:100%;box-sizing:border-box;padding:10px;margin-top:6px}}
button{{margin-top:18px;padding:10px 16px;font-weight:700;cursor:pointer}} .notice{{border-left:4px solid #b42318;padding:12px;background:#fff4f2}}
ul{{line-height:1.55}} small{{color:#666}} .muted{{color:#666}}
</style></head><body><h1>BuildWise Pro</h1><p class="muted">Professional Report Generator — preview MVP</p>{body}</body></html>"""


def _login_page(error: str = "") -> str:
    error_html = f'<p class="notice">{escape(error)}</p>' if error else ""
    return _page(
        f"""<div class="card"><h2>Preview access</h2>{error_html}
<form method="post" action="/pro/login"><label>Access key<input type="password" name="access_key" required autocomplete="current-password"></label>
<button type="submit">Enter BuildWise Pro</button></form></div>
<div class="notice">{escape(PROFESSIONAL_REVIEW_NOTICE)}</div>"""
    )


def _report_form(session: str, settings: Settings) -> str:
    csrf = csrf_token(settings, session)
    max_mb = settings.buildwise_pro_max_file_size_mb
    return _page(
        f"""<div class="notice">{escape(PROFESSIONAL_REVIEW_NOTICE)}</div>
<div class="card"><h2>Create draft report</h2>
<form method="post" action="/pro/report" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{escape(csrf)}">
<label>Project / document information<textarea name="project_information" rows="4" maxlength="2000" placeholder="Project, document number, location, inspection date, etc."></textarea></label>
<label>Drawing or sketch (required)<input type="file" name="drawing" accept="application/pdf,image/png,image/jpeg" required></label>
<small>PDF, PNG, JPG/JPEG. Max {max_mb} MB. Files are processed in memory and are not persisted by this MVP.</small>
<label>Site notes (required)<textarea name="site_notes" rows="8" maxlength="16000" required placeholder="Only record facts you actually observed or were supplied."></textarea></label>
<label>Company report template (optional PDF)<input type="file" name="template" accept="application/pdf"></label>
<label>Engineering code / standard reference (optional)<input type="text" name="standard_reference" maxlength="1200" placeholder="Example: AS 3600 — reference only; BuildWise will not invent clauses"></label>
<button type="submit">Generate draft professional report</button></form></div>
<form method="post" action="/pro/logout"><input type="hidden" name="csrf" value="{escape(csrf)}"><button type="submit">Sign out</button></form>"""
    )


def _list(items: list[str]) -> str:
    if not items:
        return "<p>None stated.</p>"
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _render_report(report: ProfessionalReport, used: int, limit: int) -> str:
    sections = [
        ("Project / document information", _list(report.project_document_information)),
        ("Summary", f"<p>{escape(report.summary)}</p>"),
        ("Observations", _list(report.observations)),
        ("Drawing / sketch findings", _list(report.drawing_sketch_findings)),
        ("Site-note findings", _list(report.site_note_findings)),
        ("Relevant technical considerations", _list(report.relevant_technical_considerations)),
        ("Referenced code / standard", _list(report.referenced_code_standard)),
        ("Recommendations", _list(report.recommendations)),
        ("Limitations / assumptions", _list(report.limitations_assumptions)),
        ("Uncertainties / missing information", _list(report.uncertainties_missing_information)),
        ("Review / sign-off", f"<p>{escape(report.review_signoff)}</p>"),
    ]
    body = '<div class="notice"><strong>DRAFT — PROFESSIONAL REVIEW REQUIRED</strong><br>' + escape(report.professional_review_notice) + "</div>"
    body += f'<p class="muted">Preview usage: {used} / {limit}. Template structure used: {"yes" if report.template_structure_used else "no"}.</p>'
    for heading, content in sections:
        body += f'<div class="card"><h2>{escape(heading)}</h2>{content}</div>'
    body += '<p><a href="/pro">Create another draft</a></p>'
    return _page(body, title="BuildWise Pro Draft Report")


def _detect_file_type(filename: str, data: bytes, *, template: bool = False) -> str:
    suffix = Path(filename).suffix.lower()
    if template:
        if suffix != ".pdf" or not data.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="Company template must be a valid PDF.")
        return "application/pdf"
    if suffix not in _ALLOWED_DRAWING_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Drawing must be PDF, PNG, JPG, or JPEG.")
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise HTTPException(status_code=400, detail="File content does not match the permitted drawing type.")


async def _read_upload(upload: UploadFile, *, max_bytes: int, template: bool = False) -> ProFile:
    filename = (upload.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="A filename is required.")
    data = await upload.read(max_bytes + 1)
    await upload.close()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Uploaded file is too large.")
    mime_type = _detect_file_type(filename, data, template=template)
    return ProFile(filename=Path(filename).name[:180], mime_type=mime_type, data=data)


def create_pro_router(settings: Settings) -> APIRouter:
    validate_pro_configuration(settings)
    router = APIRouter()
    service = ProfessionalReportService(settings)
    limiter = PreviewUsageLimiter()
    subject = stable_subject_hash(settings)

    @router.get("/pro", response_class=HTMLResponse)
    async def pro_home(request: Request) -> HTMLResponse:
        session = request.cookies.get(SESSION_COOKIE)
        if not validate_session(settings, session):
            return _secure_html(_login_page())
        return _secure_html(_report_form(session or "", settings))

    @router.post("/pro/login")
    async def pro_login(access_key: str = Form(...)) -> Response:
        if not access_key_matches(settings, access_key):
            return _secure_html(_login_page("Invalid preview access key."), status_code=401)
        session = issue_session(settings)
        response = RedirectResponse("/pro", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            SESSION_COOKIE,
            session,
            max_age=settings.buildwise_pro_session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/pro",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/pro/logout")
    async def pro_logout(request: Request, csrf: str = Form(...)) -> Response:
        session = request.cookies.get(SESSION_COOKIE)
        if not validate_session(settings, session) or not csrf_matches(settings, session or "", csrf):
            raise HTTPException(status_code=403, detail="Invalid session or CSRF token.")
        response = RedirectResponse("/pro", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(SESSION_COOKIE, path="/pro")
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/pro/report", response_class=HTMLResponse)
    async def generate_report(
        request: Request,
        csrf: str = Form(...),
        project_information: str = Form(default=""),
        site_notes: str = Form(...),
        standard_reference: str = Form(default=""),
        drawing: UploadFile = File(...),
        template: UploadFile | None = File(default=None),
    ) -> HTMLResponse:
        session = request.cookies.get(SESSION_COOKIE)
        if not validate_session(settings, session):
            raise HTTPException(status_code=401, detail="Authentication required.")
        if not csrf_matches(settings, session or "", csrf):
            raise HTTPException(status_code=403, detail="Invalid CSRF token.")
        try:
            report_request = ProfessionalReportRequest(
                project_information=project_information,
                site_notes=site_notes,
                standard_reference=standard_reference,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid report input.") from exc

        max_bytes = settings.buildwise_pro_max_file_size_bytes
        drawing_file = await _read_upload(drawing, max_bytes=max_bytes)
        template_file: ProFile | None = None
        if template is not None and (template.filename or "").strip():
            template_file = await _read_upload(template, max_bytes=max_bytes, template=True)

        usage_date = date.today()
        limit = settings.buildwise_pro_daily_request_limit
        allowed, used = await limiter.reserve(subject, usage_date, limit)
        if not allowed:
            raise HTTPException(status_code=429, detail="BuildWise Pro preview daily limit reached.")
        try:
            report = await service.generate_professional_report(
                request=report_request,
                drawing=drawing_file,
                template=template_file,
            )
        except AIServiceError as exc:
            await limiter.refund(subject, usage_date)
            logger.warning("BuildWise Pro report generation failed: %s", exc.public_message)
            raise HTTPException(status_code=502, detail=exc.public_message) from exc

        logger.info(
            "BuildWise Pro draft generated drawing_type=%s template=%s usage=%s/%s",
            drawing_file.mime_type,
            bool(template_file),
            used,
            limit,
        )
        return _secure_html(_render_report(report, used, limit))

    return router
