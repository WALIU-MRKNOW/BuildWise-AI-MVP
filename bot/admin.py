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
    template = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuildWise Admin</title>
<style>
:root{--bg:#07101d;--panel:rgba(16,29,47,.88);--panel2:#14243a;--text:#edf4ff;--muted:#93a4bb;--accent:#6ee7b7;--accent2:#60a5fa;--line:#243852}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:radial-gradient(circle at 15% 0%,rgba(96,165,250,.13),transparent 28%),radial-gradient(circle at 88% 15%,rgba(110,231,183,.09),transparent 30%),var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.12;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,black,transparent 75%)}
main{position:relative;max-width:1380px;margin:auto;padding:30px 18px 60px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}.brandline{display:flex;align-items:center;gap:10px}.pulse{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(110,231,183,.55);animation:pulse 2.2s infinite}h1{font-size:29px;margin:0}h2{font-size:17px;margin:0}.sub,.empty,.count{color:var(--muted)}.sub{margin:6px 0 0}.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}.stamp{color:var(--muted);font-size:12px}
button,input,select{font:inherit}button{appearance:none;border:1px solid var(--line);background:rgba(20,36,58,.82);color:var(--text);border-radius:10px;padding:8px 11px;font-weight:700;cursor:pointer;transition:transform .18s ease,border-color .18s ease,background .18s ease}button:hover{transform:translateY(-1px);border-color:#38557b;background:#182c47}button:disabled{opacity:.5;cursor:not-allowed;transform:none}.spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.25);border-top-color:var(--accent);border-radius:50%;margin-right:7px;vertical-align:-2px;animation:spin .75s linear infinite}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,.panel{background:var(--panel);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:15px;box-shadow:0 10px 35px rgba(0,0,0,.18)}.card{padding:16px;opacity:0;transform:translateY(10px);animation:rise .42s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease}.card:hover{transform:translateY(-3px);border-color:#315074;box-shadow:0 16px 40px rgba(0,0,0,.24)}.metric{font-size:26px;font-weight:850;margin-top:7px}.label{color:var(--muted);font-size:12px}.sections{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.panel{overflow:auto;padding:16px;opacity:0;transform:translateY(12px);animation:rise .48s cubic-bezier(.2,.8,.2,1) forwards;animation-delay:.16s;transition:border-color .2s ease,box-shadow .2s ease}.panel:hover{border-color:#2d4664;box-shadow:0 14px 42px rgba(0,0,0,.2)}.full{grid-column:1/-1}
.panelhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.search{min-width:250px;max-width:420px;width:min(420px,72vw);background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:10px;padding:8px 10px;outline:none}.search:focus,select:focus{border-color:var(--accent2)}select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:7px}.pager{display:flex;align-items:center;gap:7px}.pager button{padding:6px 9px}.page{min-width:72px;text-align:center;color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.025em;position:sticky;top:0;background:#101d2f;z-index:1}tbody tr{transition:background .16s ease}tbody tr:hover{background:rgba(96,165,250,.055)}.badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:nowrap}.message{max-width:470px;word-break:break-word}.bar{display:grid;grid-template-columns:100px minmax(120px,1fr) auto;align-items:center;gap:9px;margin:10px 0}.track{height:9px;border-radius:999px;background:rgba(147,164,187,.12);overflow:hidden}.barfill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));display:block;transform:scaleX(0);transform-origin:left;animation:grow .7s cubic-bezier(.2,.8,.2,1) forwards}.skeleton{background:linear-gradient(90deg,rgba(255,255,255,.035),rgba(255,255,255,.085),rgba(255,255,255,.035));background-size:220% 100%;animation:shimmer 1.2s linear infinite;border-radius:9px;height:72px}.toast{position:fixed;right:18px;bottom:18px;z-index:10;padding:10px 13px;border-radius:10px;background:#10253a;border:1px solid #2d526c;box-shadow:0 16px 45px rgba(0,0,0,.35);opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .2s ease,transform .2s ease}.toast.show{opacity:1;transform:translateY(0)}
@keyframes rise{to{opacity:1;transform:translateY(0)}}@keyframes grow{to{transform:scaleX(1)}}@keyframes shimmer{to{background-position:-220% 0}}@keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(110,231,183,0)}100%{box-shadow:0 0 0 0 rgba(110,231,183,0)}}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.sections{grid-template-columns:1fr}.search{min-width:210px}}@media(max-width:520px){.grid{grid-template-columns:1fr}.topbar{flex-direction:column}.panelhead{align-items:flex-start}.toolbar{width:100%}.search{width:100%;max-width:none}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.001ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.001ms!important}.card,.panel{opacity:1;transform:none}}
</style>
</head>
<body>
<main>
  <div class="topbar">
    <div><div class="brandline"><span class="pulse" aria-hidden="true"></span><h1>BuildWise Admin</h1></div><p class="sub">Private operations dashboard · server-side Supabase access only</p></div>
    <div class="actions"><span id="stamp" class="stamp">Loading…</span><button id="refresh" type="button">Refresh</button></div>
  </div>
  <div id="metrics" class="grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>
  <div class="sections">
    <section class="panel full"><div class="panelhead"><h2>Mode usage · last 30 days</h2></div><div id="modes"><div class="empty">Loading…</div></div></section>
    <section class="panel full">
      <div class="panelhead"><h2>All users</h2><div class="toolbar"><input id="userSearch" class="search" type="search" autocomplete="off" placeholder="Search name, institution, @username or Telegram ID"><span id="userCount" class="count"></span><div class="pager"><button id="userPrev" type="button">Prev</button><span id="userPage" class="page"></span><button id="userNext" type="button">Next</button></div></div></div>
      <div id="users"></div>
    </section>
    <section class="panel full">
      <div class="panelhead"><h2>Recent activity</h2><div class="toolbar"><input id="activitySearch" class="search" type="search" autocomplete="off" placeholder="Search student, Telegram ID, institution or message"><select id="activityMode"><option value="">All modes</option></select><span id="activityCount" class="count"></span></div></div>
      <div id="activity"></div>
    </section>
    <section class="panel full"><div class="panelhead"><h2>Support tickets</h2></div><div id="tickets"></div></section>
  </div>
</main>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
const CSRF=__CSRF__;
const USER_PAGE_SIZE=50;
let dashboard=null;
let userPageIndex=0;
const esc=v=>String(v??'');
const date=v=>v?new Date(v).toLocaleString():'—';
const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
function table(headers,rows){if(!rows.length)return'<div class="empty">No data yet.</div>';return`<table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`}
function td(text,cls=''){const d=document.createElement('div');d.textContent=esc(text);return`<td class="${cls}">${d.innerHTML}</td>`}
function animateNumber(el,target){const end=Number(target);if(!Number.isFinite(end)||reduceMotion){el.textContent=target;return}const start=performance.now(),duration=520;function tick(now){const p=Math.min(1,(now-start)/duration);const eased=1-Math.pow(1-p,3);el.textContent=Math.round(end*eased).toLocaleString();if(p<1)requestAnimationFrame(tick);else el.textContent=end.toLocaleString()}requestAnimationFrame(tick)}
function toast(message){const el=document.getElementById('toast');el.textContent=message;el.classList.add('show');clearTimeout(window.__toastTimer);window.__toastTimer=setTimeout(()=>el.classList.remove('show'),1800)}
function metricCards(s){const items=[['Users',s.users],['Active · 7d',s.active_users_7d],['AI requests · today',s.requests_today],['Open tickets',s.open_tickets]];const root=document.getElementById('metrics');root.innerHTML=items.map(([a,b],i)=>`<div class="card" style="animation-delay:${i*45}ms"><div class="label">${a}</div><div class="metric" data-value="${esc(b)}">0</div></div>`).join('');root.querySelectorAll('.metric').forEach(el=>animateNumber(el,el.dataset.value))}
function renderModes(d){const modes=Object.entries(d.mode_usage).sort((a,b)=>b[1]-a[1]);const max=Math.max(1,...modes.map(x=>x[1]));document.getElementById('modes').innerHTML=modes.length?modes.map(([m,n],i)=>`<div class="bar"><div>${esc(m)}</div><div class="track"><span class="barfill" style="width:${Math.max(3,Math.round(n/max*100))}%;animation-delay:${120+i*55}ms"></span></div><b>${n}</b></div>`).join(''):'<div class="empty">No mode usage yet.</div>';const modeSelect=document.getElementById('activityMode');const selected=modeSelect.value;modeSelect.innerHTML='<option value="">All modes</option>'+modes.map(([m])=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');if([...modeSelect.options].some(o=>o.value===selected))modeSelect.value=selected}
function userMatches(u,q){if(!q)return true;return [u.display_name,u.university,u.username,u.telegram_id].map(esc).join(' ').toLowerCase().includes(q)}
function renderUsers(){if(!dashboard)return;const q=document.getElementById('userSearch').value.trim().toLowerCase();const filtered=dashboard.users.filter(u=>userMatches(u,q));const pages=Math.max(1,Math.ceil(filtered.length/USER_PAGE_SIZE));userPageIndex=Math.min(userPageIndex,pages-1);const start=userPageIndex*USER_PAGE_SIZE;const pageRows=filtered.slice(start,start+USER_PAGE_SIZE);document.getElementById('users').innerHTML=table(['Name','Institution','Username','Telegram ID','Activity · 30d','Last activity · 30d','Joined'],pageRows.map(u=>`<tr>${td(u.display_name)}${td(u.university)}${td(u.username?'@'+u.username:'—')}${td(u.telegram_id||'—','mono')}${td(u.activity_30d??0)}${td(date(u.last_activity_30d))}${td(date(u.created_at))}</tr>`));document.getElementById('userCount').textContent=`${filtered.length.toLocaleString()} of ${dashboard.users.length.toLocaleString()} users`;document.getElementById('userPage').textContent=`Page ${userPageIndex+1} / ${pages}`;document.getElementById('userPrev').disabled=userPageIndex<=0;document.getElementById('userNext').disabled=userPageIndex>=pages-1}
function activityMatches(a,q,mode){if(mode&&a.mode!==mode)return false;if(!q)return true;return [a.display_name,a.username,a.telegram_id,a.university,a.mode,a.category,a.message].map(esc).join(' ').toLowerCase().includes(q)}
function renderActivity(){if(!dashboard)return;const q=document.getElementById('activitySearch').value.trim().toLowerCase();const mode=document.getElementById('activityMode').value;const rows=dashboard.recent_activity.filter(a=>activityMatches(a,q,mode));document.getElementById('activity').innerHTML=table(['When','Name','Username','Telegram ID','Institution','Mode','Category','Message'],rows.map(a=>`<tr>${td(date(a.created_at))}${td(a.display_name)}${td(a.username?'@'+a.username:'—')}${td(a.telegram_id||'—','mono')}${td(a.university)}<td><span class="badge">${esc(a.mode)}</span></td>${td(a.category||'—')}${td(a.message,'message')}</tr>`));document.getElementById('activityCount').textContent=`${rows.length.toLocaleString()} of ${dashboard.recent_activity.length.toLocaleString()} events`}
function renderTickets(){document.getElementById('tickets').innerHTML=table(['Created','User','Telegram ID','Message','Status'],dashboard.tickets.map(t=>`<tr>${td(date(t.created_at))}${td(t.display_name+(t.username?' · @'+t.username:''))}${td(t.telegram_id||'—','mono')}${td(t.message,'message')}<td><select data-id="${esc(t.id)}"><option value="open" ${t.status==='open'?'selected':''}>Open</option><option value="in_progress" ${t.status==='in_progress'?'selected':''}>In Progress</option><option value="resolved" ${t.status==='resolved'?'selected':''}>Resolved</option></select></td></tr>`));document.querySelectorAll('select[data-id]').forEach(el=>el.addEventListener('change',async()=>{el.disabled=true;const r=await fetch('/admin/api/tickets/'+encodeURIComponent(el.dataset.id),{method:'PATCH',headers:{'Content-Type':'application/json','X-BuildWise-CSRF':CSRF},body:JSON.stringify({status:el.value})});el.disabled=false;if(!r.ok){toast('Status update failed');await load(false);return}toast('Ticket status updated')}))}
function render(d){dashboard=d;metricCards(d.summary);renderModes(d);renderUsers();renderActivity();renderTickets()}
async function load(showToast=true){const btn=document.getElementById('refresh');btn.disabled=true;btn.innerHTML='<span class="spin"></span>Refreshing';try{const r=await fetch('/admin/api/dashboard',{cache:'no-store'});if(!r.ok)throw new Error('Could not load admin data');const d=await r.json();render(d);document.getElementById('stamp').textContent='Updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});if(showToast)toast('Dashboard refreshed')}finally{btn.disabled=false;btn.textContent='Refresh'}}
document.getElementById('refresh').addEventListener('click',()=>load(true));
document.getElementById('userSearch').addEventListener('input',()=>{userPageIndex=0;renderUsers()});
document.getElementById('userPrev').addEventListener('click',()=>{if(userPageIndex>0){userPageIndex--;renderUsers()}});
document.getElementById('userNext').addEventListener('click',()=>{userPageIndex++;renderUsers()});
document.getElementById('activitySearch').addEventListener('input',renderActivity);
document.getElementById('activityMode').addEventListener('change',renderActivity);
load(false).catch(e=>{document.getElementById('metrics').innerHTML='<div class="empty">'+esc(e.message)+'</div>';toast(e.message)});
</script>
</body>
</html>'''
    return template.replace("__CSRF__", repr(csrf))
