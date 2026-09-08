"""Private server-rendered admin dashboard and JSON endpoints."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from bot.config import Settings
from bot.services.admin_service import AdminService, AdminServiceError

logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)


class TicketStatusUpdate(BaseModel):
    status: str


def admin_configuration_error(settings: Settings) -> str | None:
    username = settings.admin_username.strip()
    password = (
        settings.admin_password.get_secret_value().strip()
        if settings.admin_password is not None
        else ""
    )
    if not username:
        return "ADMIN_USERNAME is blank"
    if len(password) < 16:
        return "ADMIN_PASSWORD must contain at least 16 characters"
    return None


def log_admin_configuration(settings: Settings) -> None:
    error = admin_configuration_error(settings)
    if error:
        logger.warning("BuildWise admin dashboard disabled: %s", error)
    else:
        logger.info("BuildWise admin dashboard enabled at /admin")


def create_admin_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    service = AdminService(settings)

    def configured_credentials() -> tuple[str, str]:
        error = admin_configuration_error(settings)
        if error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Admin dashboard is not configured: {error}",
            )
        return (
            settings.admin_username.strip(),
            settings.admin_password.get_secret_value().strip(),  # type: ignore[union-attr]
        )

    def require_admin(
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> None:
        expected_user, expected_password = configured_credentials()
        if credentials is None or not (
            secrets.compare_digest(credentials.username, expected_user)
            and secrets.compare_digest(credentials.password, expected_password)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="BuildWise Admin", charset="UTF-8"'},
            )

    def csrf_token() -> str:
        _, password = configured_credentials()
        return hmac.new(
            password.encode("utf-8"),
            b"buildwise-admin-ticket-status-v1",
            hashlib.sha256,
        ).hexdigest()

    def require_csrf(x_buildwise_csrf: str | None = Header(default=None)) -> None:
        if not x_buildwise_csrf or not secrets.compare_digest(
            x_buildwise_csrf, csrf_token()
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    @router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    async def admin_page() -> HTMLResponse:
        return HTMLResponse(
            _dashboard_html(csrf_token()),
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            },
        )

    @router.get("/admin/api/dashboard", dependencies=[Depends(require_admin)])
    async def dashboard_data() -> Response:
        try:
            payload = await service.dashboard()
        except AdminServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Admin data unavailable",
            ) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.patch(
        "/admin/api/tickets/{ticket_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin), Depends(require_csrf)],
    )
    async def update_ticket(ticket_id: str, update: TicketStatusUpdate) -> Response:
        try:
            await service.update_ticket_status(ticket_id, update.status)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except AdminServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _dashboard_html(csrf: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuildWise Admin</title>
<style>
:root{{--bg:#07101d;--panel:rgba(16,29,47,.9);--panel2:#14243a;--text:#edf4ff;--muted:#93a4bb;--accent:#6ee7b7;--accent2:#60a5fa;--line:#243852;--danger:#fca5a5}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:radial-gradient(circle at 12% 0%,rgba(96,165,250,.14),transparent 30%),radial-gradient(circle at 90% 12%,rgba(110,231,183,.08),transparent 28%),var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1380px;margin:auto;padding:28px 18px 56px}}
.topbar{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}}
.brand{{display:flex;gap:10px;align-items:center}}.pulse{{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(110,231,183,.55);animation:pulse 2.2s infinite}}
h1{{font-size:28px;margin:0}}h2{{font-size:17px;margin:0 0 12px}}.sub,.empty{{color:var(--muted)}}.sub{{margin:5px 0 0}}
.actions{{display:flex;align-items:center;gap:9px;flex-wrap:wrap}}button,input{{font:inherit}}button{{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:10px;padding:8px 11px;font-weight:700;cursor:pointer;transition:.18s ease}}button:hover{{transform:translateY(-1px);border-color:#3c5d83}}button:disabled{{opacity:.6;cursor:wait;transform:none}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:15px;box-shadow:0 12px 34px rgba(0,0,0,.2);backdrop-filter:blur(12px)}}.card{{padding:16px;opacity:0;transform:translateY(10px);animation:rise .42s ease forwards}}.metric{{font-size:27px;font-weight:850;margin-top:6px}}.label{{font-size:12px;color:var(--muted)}}
.sections{{display:grid;grid-template-columns:1fr;gap:14px;margin-top:14px}}.panel{{padding:16px;overflow:auto;opacity:0;transform:translateY(10px);animation:rise .48s ease .12s forwards}}.panelhead{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}}.search{{width:min(340px,100%);background:#0d1c2e;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:8px 10px;outline:none}}.search:focus{{border-color:var(--accent2)}}
table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:11px;color:var(--muted);text-transform:uppercase;position:sticky;top:0;background:#0e1c2d}}tbody tr:hover{{background:rgba(96,165,250,.05)}}.badge{{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);font-size:12px}}.message{{max-width:480px;word-break:break-word}}.id{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}.bar{{display:grid;grid-template-columns:100px minmax(130px,1fr) auto;align-items:center;gap:9px;margin:9px 0}}.track{{height:9px;background:rgba(147,164,187,.12);border-radius:999px;overflow:hidden}}.fill{{height:100%;display:block;background:linear-gradient(90deg,var(--accent),var(--accent2));transform:scaleX(0);transform-origin:left;animation:grow .7s ease forwards}}
select{{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px}}.toast{{position:fixed;right:18px;bottom:18px;background:#10253a;border:1px solid #2d526c;border-radius:10px;padding:10px 13px;opacity:0;transform:translateY(8px);transition:.2s ease;pointer-events:none}}.toast.show{{opacity:1;transform:translateY(0)}}
@keyframes rise{{to{{opacity:1;transform:none}}}}@keyframes grow{{to{{transform:scaleX(1)}}}}@keyframes pulse{{70%{{box-shadow:0 0 0 8px rgba(110,231,183,0)}}100%{{box-shadow:0 0 0 0 rgba(110,231,183,0)}}}}
@media(max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}.topbar{{flex-direction:column}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
@media(prefers-reduced-motion:reduce){{*,*:before,*:after{{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}}.card,.panel{{opacity:1;transform:none}}}}
</style>
</head>
<body>
<main>
<div class="topbar"><div><div class="brand"><span class="pulse"></span><h1>BuildWise Admin</h1></div><p class="sub">Private operations dashboard · user IDs, onboarding and activity</p></div><div class="actions"><span id="stamp" class="sub">Loading…</span><button id="refresh" type="button">Refresh</button></div></div>
<div id="metrics" class="grid"></div>
<div class="sections">
<section class="panel"><h2>Mode usage · last 30 days</h2><div id="modes"></div></section>
<section class="panel"><div class="panelhead"><h2>All users</h2><input id="userSearch" class="search" placeholder="Search name, university, username or Telegram ID"></div><div id="users"></div></section>
<section class="panel"><div class="panelhead"><h2>Recent activity</h2><span class="sub">Latest 100 saved interactions</span></div><div id="activity"></div></section>
<section class="panel"><h2>Support tickets</h2><div id="tickets"></div></section>
</div>
</main><div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
const CSRF={csrf!r};let DATA=null;
const esc=v=>String(v??'');
const date=v=>v?new Date(v).toLocaleString():'—';
function safe(v){{const d=document.createElement('div');d.textContent=esc(v);return d.innerHTML}}
function td(v,cls=''){{return `<td class="${{cls}}">${{safe(v)}}</td>`}}
function table(headers,rows){{if(!rows.length)return'<div class="empty">No data yet.</div>';return `<table><thead><tr>${{headers.map(h=>`<th>${{safe(h)}}</th>`).join('')}}</tr></thead><tbody>${{rows.join('')}}</tbody></table>`}}
function toast(msg){{const el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');clearTimeout(window.__t);window.__t=setTimeout(()=>el.classList.remove('show'),1800)}}
function metrics(s){{const items=[['Users',s.users],['Active · 7d',s.active_users_7d],['AI requests · today',s.requests_today],['Open tickets',s.open_tickets]];document.getElementById('metrics').innerHTML=items.map(([a,b],i)=>`<div class="card" style="animation-delay:${{i*45}}ms"><div class="label">${{safe(a)}}</div><div class="metric">${{Number(b||0).toLocaleString()}}</div></div>`).join('')}}
function renderUsers(){{if(!DATA)return;const q=document.getElementById('userSearch').value.trim().toLowerCase();const rows=DATA.users.filter(u=>!q||[u.display_name,u.university,u.username,u.telegram_id].some(v=>esc(v).toLowerCase().includes(q))).map(u=>`<tr>${{td(u.display_name)}}${{td(u.university||'—')}}${{td(u.username?'@'+u.username:'—')}}${{td(u.telegram_id,'id')}}${{td(u.onboarding_completed?'Yes':'No')}}${{td(date(u.created_at))}}${{td(date(u.updated_at))}}</tr>`);document.getElementById('users').innerHTML=table(['Name','Institution','Username','Telegram ID','Onboarded','Joined','Last seen'],rows)}}
function render(d){{DATA=d;metrics(d.summary);const modes=Object.entries(d.mode_usage||{{}}).sort((a,b)=>b[1]-a[1]);const max=Math.max(1,...modes.map(x=>x[1]));document.getElementById('modes').innerHTML=modes.length?modes.map(([m,n],i)=>`<div class="bar"><div>${{safe(m)}}</div><div class="track"><span class="fill" style="width:${{Math.max(3,Math.round(n/max*100))}}%;animation-delay:${{i*55}}ms"></span></div><b>${{n}}</b></div>`).join(''):'<div class="empty">No mode usage yet.</div>';renderUsers();document.getElementById('activity').innerHTML=table(['When','Student','Telegram ID','Mode','Message'],(d.recent_activity||[]).map(a=>`<tr>${{td(date(a.created_at))}}${{td((a.display_name||'Student')+(a.university?' · '+a.university:''))}}${{td(a.telegram_id||'—','id')}}<td><span class="badge">${{safe(a.mode)}}</span></td>${{td(a.message,'message')}}</tr>`));document.getElementById('tickets').innerHTML=table(['When','Student','Telegram ID','Message','Status'],(d.tickets||[]).map(t=>`<tr>${{td(date(t.created_at))}}${{td(t.display_name||'Student')}}${{td(t.telegram_id||'—','id')}}${{td(t.message,'message')}}<td><select data-id="${{safe(t.id)}}"><option value="open" ${{t.status==='open'?'selected':''}}>Open</option><option value="in_progress" ${{t.status==='in_progress'?'selected':''}}>In progress</option><option value="resolved" ${{t.status==='resolved'?'selected':''}}>Resolved</option></select></td></tr>`));document.querySelectorAll('select[data-id]').forEach(el=>el.addEventListener('change',()=>saveTicket(el)));document.getElementById('stamp').textContent='Updated '+date(d.generated_at)}}
async function saveTicket(el){{el.disabled=true;try{{const r=await fetch('/admin/api/tickets/'+encodeURIComponent(el.dataset.id),{{method:'PATCH',headers:{{'Content-Type':'application/json','X-BuildWise-CSRF':CSRF}},body:JSON.stringify({{status:el.value}})}});if(!r.ok)throw new Error();toast('Ticket updated')}}catch(e){{toast('Could not update ticket')}}finally{{el.disabled=false}}}}
async function load(){{const btn=document.getElementById('refresh');btn.disabled=true;btn.textContent='Refreshing…';try{{const r=await fetch('/admin/api/dashboard',{{cache:'no-store'}});if(!r.ok)throw new Error();render(await r.json())}}catch(e){{toast('Admin data unavailable')}}finally{{btn.disabled=false;btn.textContent='Refresh'}}}}
document.getElementById('refresh').addEventListener('click',load);document.getElementById('userSearch').addEventListener('input',renderUsers);load();
</script>
</body></html>'''
