"""FastAPI routes and browser WebMCP registration for the BuildWise POC."""

from __future__ import annotations

from html import escape
import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.config import Settings
from bot.webmcp.auth import (
    SESSION_COOKIE,
    access_key_matches,
    csrf_matches,
    csrf_token,
    issue_session,
    subject_hash,
    validate_session,
    validate_webmcp_configuration,
)
from bot.webmcp.service import PracticeQuizInput, WebMCPQuizService, WebMCPToolError

logger = logging.getLogger(__name__)


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'self'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }


def _login_page(message: str = "") -> str:
    notice = f'<p class="error">{escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BuildWise WebMCP Preview</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:680px;margin:10vh auto;padding:24px;background:#f6f8f7;color:#123}}
main{{background:white;padding:28px;border-radius:18px;box-shadow:0 8px 30px #0001}}
input,button{{font:inherit;padding:12px;border-radius:10px;border:1px solid #ccd;width:100%;box-sizing:border-box}}
button{{margin-top:12px;background:#123c32;color:white;border:0;font-weight:700;cursor:pointer}}
.error{{color:#9d1c1c}}
small{{color:#667}}
</style>
</head>
<body><main>
<h1>BuildWise WebMCP</h1>
<p>Private proof-of-concept for the WebMCP Challenge.</p>
{notice}
<form method="post" action="/app/login" autocomplete="off">
<label for="access_key">Preview access key</label>
<input id="access_key" name="access_key" type="password" required maxlength="256">
<button type="submit">Open BuildWise</button>
</form>
<p><small>The access key is exchanged server-side for an HttpOnly session cookie and is never placed in page JavaScript.</small></p>
</main></body></html>"""


def _app_page(csrf: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="buildwise-csrf" content="{escape(csrf, quote=True)}">
<title>BuildWise WebMCP</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:20px;background:#f6f8f7;color:#123}}
.card{{background:white;border-radius:18px;padding:24px;margin:16px 0;box-shadow:0 8px 30px #0001}}
.badge{{display:inline-block;padding:6px 10px;border-radius:999px;background:#e7f3ee;color:#174a3b;font-weight:700}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f3;padding:16px;border-radius:12px}}
</style>
<script src="/app/webmcp.js" defer></script>
</head>
<body>
<h1>BuildWise AI · WebMCP</h1>
<p>Student → ChatGPT/Codex → BuildWise site tool → existing BuildWise quiz engine.</p>
<div class="card">
<span class="badge">Tool: generate_practice_quiz</span>
<h2>Agent-ready practice</h2>
<p>Open this page in ChatGPT's built-in browser and ask: <strong>“Use BuildWise to generate a 5-question practice quiz about reinforced concrete.”</strong></p>
<p id="webmcp-status">Checking WebMCP support…</p>
</div>
<div class="card">
<h2>Latest tool result</h2>
<pre id="tool-result">No quiz generated yet.</pre>
</div>
</body></html>"""


WEBMCP_SCRIPT = r"""(() => {
  const statusEl = document.getElementById('webmcp-status');
  const resultEl = document.getElementById('tool-result');
  const csrf = document.querySelector('meta[name="buildwise-csrf"]')?.content || '';

  const renderResult = (data) => {
    if (!resultEl) return;
    resultEl.textContent = JSON.stringify(data, null, 2);
  };

  const callQuiz = async (input, options = {}) => {
    const response = await fetch('/app/api/tools/generate-practice-quiz', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-BuildWise-CSRF': csrf,
      },
      body: JSON.stringify(input),
      signal: options.signal,
    });
    const data = await response.json().catch(() => ({error: 'BuildWise returned an invalid response.'}));
    if (!response.ok) {
      throw new Error(data.error || data.detail || `BuildWise request failed (${response.status})`);
    }
    renderResult(data);
    return data;
  };

  const register = async () => {
    if (!document.modelContext || typeof document.modelContext.registerTool !== 'function') {
      if (statusEl) statusEl.textContent = 'WebMCP is not available in this browser. Open this page in a supported ChatGPT/Codex browser or WebMCP-enabled Chromium.';
      return;
    }
    await document.modelContext.registerTool({
      name: 'generate_practice_quiz',
      title: 'Generate BuildWise Practice Quiz',
      description: 'Generate a structured student practice quiz with BuildWise. Use this when the user wants multiple-choice practice questions on a study, engineering, construction, architecture, or general academic topic.',
      inputSchema: {
        type: 'object',
        properties: {
          topic: {
            type: 'string',
            minLength: 2,
            maxLength: 200,
            description: 'The topic the student wants to practice, for example reinforced concrete.'
          },
          difficulty: {
            type: 'string',
            enum: ['easy', 'medium', 'hard'],
            default: 'medium',
            description: 'Desired practice difficulty.'
          },
          count: {
            type: 'integer',
            minimum: 1,
            maximum: 10,
            default: 5,
            description: 'Number of multiple-choice questions to generate.'
          }
        },
        required: ['topic'],
        additionalProperties: false
      },
      annotations: {
        readOnlyHint: false,
        untrustedContentHint: true
      },
      execute: callQuiz
    });
    if (statusEl) statusEl.textContent = 'WebMCP ready. ChatGPT/Codex can discover generate_practice_quiz on this page.';
  };

  register().catch((error) => {
    console.error('BuildWise WebMCP registration failed', error);
    if (statusEl) statusEl.textContent = 'WebMCP registration failed. See the browser console for details.';
  });
})();
"""


def create_webmcp_router(settings: Settings) -> APIRouter:
    validate_webmcp_configuration(settings)
    router = APIRouter()
    service = WebMCPQuizService(settings)

    def require_session(request: Request) -> str:
        token = request.cookies.get(SESSION_COOKIE)
        if not validate_session(settings, token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="WebMCP authentication required")
        return token or ""

    @router.get("/app", response_class=HTMLResponse)
    async def webmcp_app(request: Request) -> HTMLResponse:
        token = request.cookies.get(SESSION_COOKIE)
        if not validate_session(settings, token):
            return HTMLResponse(_login_page(), headers=_security_headers())
        return HTMLResponse(
            _app_page(csrf_token(settings, token or "")), headers=_security_headers()
        )

    @router.post("/app/login")
    async def webmcp_login(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > 4096:
            return HTMLResponse(
                _login_page("Invalid login request."),
                status_code=status.HTTP_400_BAD_REQUEST,
                headers=_security_headers(),
            )
        values = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        supplied = values.get("access_key", [""])[0]
        if not access_key_matches(settings, supplied):
            logger.warning("Rejected WebMCP preview login")
            return HTMLResponse(
                _login_page("Invalid access key."),
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers=_security_headers(),
            )
        token = issue_session(settings)
        response = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.webmcp_session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/app",
        )
        for name, value in _security_headers().items():
            response.headers[name] = value
        logger.info("WebMCP preview session issued")
        return response

    @router.get("/app/webmcp.js")
    async def webmcp_script(request: Request) -> Response:
        require_session(request)
        return Response(
            WEBMCP_SCRIPT,
            media_type="application/javascript",
            headers=_security_headers(),
        )

    @router.post("/app/api/tools/generate-practice-quiz")
    async def generate_practice_quiz(
        request: Request,
        payload: PracticeQuizInput,
        x_buildwise_csrf: str | None = Header(default=None),
    ) -> Response:
        token = require_session(request)
        if not csrf_matches(settings, token, x_buildwise_csrf):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
        try:
            result = await service.generate(payload, subject_hash=subject_hash(settings))
        except WebMCPToolError as exc:
            return Response(
                json.dumps({"error": exc.public_message}),
                status_code=exc.status_code,
                media_type="application/json",
                headers=_security_headers(),
            )
        return Response(
            result.model_dump_json(),
            media_type="application/json",
            headers=_security_headers(),
        )

    return router
