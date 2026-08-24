"""Private server-rendered admin dashboard and JSON endpoints."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from bot.config import Settings
from bot.services.admin_service import AdminService, AdminServiceError

security = HTTPBasic(auto_error=False)


class TicketStatusUpdate(BaseModel):
    status: str


def create_admin_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    service = AdminService(settings)

    def configured_credentials() -> tuple[str, str]:
        username = settings.admin_username.strip()
        password = (
            settings.admin_password.get_secret_value().strip()
            if settings.admin_password is not None
            else ""
        )
        if not username or len(password) < 16:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return username, password

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
        if not x_buildwise_csrf or not secrets.compare_digest(x_buildwise_csrf, csrf_token()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    @router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    async def admin_page() -> HTMLResponse:
        html = _dashboard_html(csrf_token())
        return HTMLResponse(
            html,
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
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Admin data unavailable") from exc
        from fastapi.responses import JSONResponse

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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except AdminServiceError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _dashboard_html(csrf: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuildWise Admin</title>
<style>
:root{{--bg:#08111f;--panel:#101d2f;--panel2:#14243a;--text:#edf4ff;--muted:#93a4bb;--accent:#6ee7b7;--warn:#fbbf24;--danger:#fb7185;--line:#243852}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1280px;margin:auto;padding:28px 18px 60px}} h1{{font-size:28px;margin:0}} h2{{font-size:17px;margin:0 0 14px}} .sub{{color:var(--muted);margin:5px 0 24px}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}} .card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}}
.metric{{font-size:25px;font-weight:800;margin-top:7px}} .label,.meta{{color:var(--muted);font-size:12px}} .sections{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}
.panel{{overflow:auto}} table{{width:100%;border-collapse:collapse;min-width:620px}} th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
.badge{{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);font-size:12px}} select{{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px}} .full{{grid-column:1/-1}}
.message{{max-width:430px;white-space:normal;word-break:break-word}} .bar{{display:flex;align-items:center;gap:8px;margin:8px 0}} .bar span{{height:8px;border-radius:8px;background:var(--accent);display:block}} .empty{{color:var(--muted);padding:16px 0}}
@media(max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}.sections{{grid-template-columns:1fr}}}} @media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main>
<h1>BuildWise Admin</h1><p class="sub">Private operations dashboard · server-side Supabase access only</p>
<div id="metrics" class="grid"></div>
<div class="sections">
<section class="panel"><h2>Mode usage · last 30 days</h2><div id="modes"></div></section>
<section class="panel"><h2>Recent users</h2><div id="users"></div></section>
<section class="panel full"><h2>Recent activity</h2><div id="activity"></div></section>
<section class="panel full"><h2>Support tickets</h2><div id="tickets"></div></section>
</div></main>
<script>
const CSRF={csrf!r};
const esc=v=>String(v??'');
const date=v=>v?new Date(v).toLocaleString():'—';
function table(headers, rows){{if(!rows.length)return '<div class="empty">No data yet.</div>';return `<table><thead><tr>${{headers.map(h=>`<th>${{h}}</th>`).join('')}}</tr></thead><tbody>${{rows.join('')}}</tbody></table>`}}
function td(text,cls=''){{const d=document.createElement('div');d.textContent=esc(text);return `<td class="${{cls}}">${{d.innerHTML}}</td>`}}
async function load(){{const r=await fetch('/admin/api/dashboard',{{cache:'no-store'}});if(!r.ok)throw new Error('Could not load admin data');const d=await r.json();
const s=d.summary;document.getElementById('metrics').innerHTML=[['Users',s.users],['Active · 7d',s.active_users_7d],['AI requests · today',s.requests_today],['Conversations · 30d',s.conversations_30d],['Open tickets',s.open_tickets]].map(([a,b])=>`<div class="card"><div class="label">${{a}}</div><div class="metric">${{b}}</div></div>`).join('');
const modes=Object.entries(d.mode_usage).sort((a,b)=>b[1]-a[1]);const max=Math.max(1,...modes.map(x=>x[1]));document.getElementById('modes').innerHTML=modes.length?modes.map(([m,n])=>`<div class="bar"><div style="width:90px">${{esc(m)}}</div><span style="width:${{Math.max(5,Math.round(n/max*220))}}px"></span><b>${{n}}</b></div>`).join(''):'<div class="empty">No mode usage yet.</div>';
document.getElementById('users').innerHTML=table(['User','Telegram','Joined'],d.users.map(u=>`<tr>${{td(u.display_name)}}${{td(u.username?'@'+u.username:u.telegram_id)}}${{td(date(u.created_at))}}</tr>`));
document.getElementById('activity').innerHTML=table(['When','User','Mode','Message'],d.recent_activity.map(a=>`<tr>${{td(date(a.created_at))}}${{td(a.display_name)}}<td><span class="badge">${{esc(a.mode)}}</span></td>${{td(a.message,'message')}}</tr>`));
document.getElementById('tickets').innerHTML=table(['Created','User','Message','Status'],d.tickets.map(t=>`<tr>${{td(date(t.created_at))}}${{td(t.display_name+(t.username?' · @'+t.username:''))}}${{td(t.message,'message')}}<td><select data-id="${{esc(t.id)}}"><option value="open" ${{t.status==='open'?'selected':''}}>Open</option><option value="in_progress" ${{t.status==='in_progress'?'selected':''}}>In Progress</option><option value="resolved" ${{t.status==='resolved'?'selected':''}}>Resolved</option></select></td></tr>`));
document.querySelectorAll('select[data-id]').forEach(el=>el.addEventListener('change',async()=>{{el.disabled=true;const r=await fetch('/admin/api/tickets/'+encodeURIComponent(el.dataset.id),{{method:'PATCH',headers:{{'Content-Type':'application/json','X-BuildWise-CSRF':CSRF}},body:JSON.stringify({{status:el.value}})}});el.disabled=false;if(!r.ok){{alert('Status update failed');await load();}}}}));
}}
load().catch(e=>{{document.body.insertAdjacentHTML('beforeend','<p style="padding:20px;color:#fb7185">'+esc(e.message)+'</p>')}});
</script></body></html>'''
