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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuildWise Admin</title><style>
:root{{--bg:#07101d;--panel:rgba(16,29,47,.86);--panel2:#14243a;--text:#edf4ff;--muted:#93a4bb;--accent:#6ee7b7;--accent2:#60a5fa;--line:#243852}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 15% 0%,rgba(96,165,250,.13),transparent 28%),radial-gradient(circle at 88% 15%,rgba(110,231,183,.09),transparent 30%),var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;overflow-x:hidden}}body:before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.12;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,black,transparent 75%)}}main{{position:relative;max-width:1420px;margin:auto;padding:30px 18px 60px}}.topbar{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}}.brandline{{display:flex;align-items:center;gap:10px}}.pulse{{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(110,231,183,.55);animation:pulse 2.2s infinite}}h1{{font-size:29px;margin:0}}h2{{font-size:17px;margin:0}}.sub,.empty,.muted{{color:var(--muted)}}.sub{{margin:6px 0 0}}.actions,.panelhead,.pager{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}.actions{{justify-content:flex-end}}.panelhead{{justify-content:space-between;margin-bottom:14px}}.stamp{{color:var(--muted);font-size:12px}}button,input,select{{font:inherit}}button{{appearance:none;border:1px solid var(--line);background:rgba(20,36,58,.82);color:var(--text);border-radius:10px;padding:8px 11px;font-weight:700;cursor:pointer;transition:transform .18s ease,border-color .18s ease,background .18s ease}}button:hover{{transform:translateY(-1px);border-color:#38557b;background:#182c47}}button:disabled{{opacity:.5;cursor:not-allowed;transform:none}}input,select{{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px 10px}}input{{min-width:250px}}.spin{{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.25);border-top-color:var(--accent);border-radius:50%;margin-right:7px;vertical-align:-2px;animation:spin .75s linear infinite}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card,.panel{{background:var(--panel);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:15px;box-shadow:0 10px 35px rgba(0,0,0,.18)}}.card{{padding:16px;opacity:0;transform:translateY(10px);animation:rise .42s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease}}.card:hover{{transform:translateY(-3px);border-color:#315074;box-shadow:0 16px 40px rgba(0,0,0,.24)}}.metric{{font-size:26px;font-weight:850;margin-top:7px}}.label{{color:var(--muted);font-size:12px}}.sections{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.panel{{overflow:auto;padding:16px;opacity:0;transform:translateY(12px);animation:rise .48s cubic-bezier(.2,.8,.2,1) forwards;animation-delay:.16s;transition:border-color .2s ease,box-shadow .2s ease}}.panel:hover{{border-color:#2d4664;box-shadow:0 14px 42px rgba(0,0,0,.2)}}.full{{grid-column:1/-1}}table{{width:100%;border-collapse:collapse;min-width:820px}}th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:11px;text-transform:uppercase;white-space:nowrap}}tbody tr{{transition:background .16s ease}}tbody tr:hover{{background:rgba(96,165,250,.055)}}.badge{{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);font-size:12px;white-space:nowrap}}.badge.founder{{border:1px solid rgba(110,231,183,.5)}}.badge.pilot{{border:1px solid rgba(96,165,250,.5)}}select.saving{{opacity:.65}}select.saved{{border-color:var(--accent)}}.message{{max-width:460px;word-break:break-word}}.bar{{display:grid;grid-template-columns:100px minmax(120px,1fr) auto;align-items:center;gap:9px;margin:10px 0}}.track{{height:9px;border-radius:999px;background:rgba(147,164,187,.12);overflow:hidden}}.barfill{{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));display:block;transform:scaleX(0);transform-origin:left;animation:grow .7s cubic-bezier(.2,.8,.2,1) forwards}}.skeleton{{background:linear-gradient(90deg,rgba(255,255,255,.035),rgba(255,255,255,.085),rgba(255,255,255,.035));background-size:220% 100%;animation:shimmer 1.2s linear infinite;border-radius:9px;height:72px}}.pager{{justify-content:flex-end;margin-top:12px}}.count{{margin-right:auto;color:var(--muted)}}.toast{{position:fixed;right:18px;bottom:18px;z-index:10;padding:10px 13px;border-radius:10px;background:#10253a;border:1px solid #2d526c;box-shadow:0 16px 45px rgba(0,0,0,.35);opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .2s ease,transform .2s ease}}.toast.show{{opacity:1;transform:translateY(0)}}@keyframes rise{{to{{opacity:1;transform:translateY(0)}}}}@keyframes grow{{to{{transform:scaleX(1)}}}}@keyframes shimmer{{to{{background-position:-220% 0}}}}@keyframes spin{{to{{transform:rotate(360deg)}}}}@keyframes pulse{{70%{{box-shadow:0 0 0 8px rgba(110,231,183,0)}}100%{{box-shadow:0 0 0 0 rgba(110,231,183,0)}}}}@media(max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr)}}.sections{{grid-template-columns:1fr}}}}@media(max-width:620px){{.grid{{grid-template-columns:1fr}}.topbar{{flex-direction:column}}input{{min-width:100%;width:100%}}.panelhead{{align-items:stretch}}}}@media(prefers-reduced-motion:reduce){{*,*:before,*:after{{animation-duration:.001ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.001ms!important}}.card,.panel{{opacity:1;transform:none}}}}
</style></head><body><main><div class="topbar"><div><div class="brandline"><span class="pulse" aria-hidden="true"></span><h1>BuildWise Admin</h1></div><p class="sub">Private operations dashboard · student IDs, onboarding and activity in one place</p></div><div class="actions"><span id="stamp" class="stamp">Loading…</span><button id="refresh" type="button">Refresh</button></div></div><div id="metrics" class="grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div><div class="sections"><section class="panel full"><div class="panelhead"><h2>Mode usage · last 30 days</h2></div><div id="modes"><div class="empty">Loading…</div></div></section><section class="panel full"><div class="panelhead"><div><h2>All users</h2><div class="muted">Numeric Telegram IDs are always shown.</div></div><div class="actions"><input id="user-search" type="search" placeholder="Search name, institution, username or ID"><select id="user-page-size"><option value="25">25 / page</option><option value="50">50 / page</option><option value="100">100 / page</option></select></div></div><div id="users"></div><div class="pager"><span id="user-count" class="count"></span><button id="user-prev" type="button">Previous</button><span id="user-page" class="muted"></span><button id="user-next" type="button">Next</button></div></section><section class="panel full"><div class="panelhead"><div><h2>Recent activity</h2><div class="muted">Operational events remain visible even when a student has history saving off.</div></div></div><div id="activity"></div></section><section class="panel full"><div class="panelhead"><h2>Support tickets</h2></div><div id="tickets"></div></section></div></main><div id="toast" class="toast" role="status" aria-live="polite"></div><script>
const CSRF={csrf!r};const esc=v=>String(v??'');const date=v=>v?new Date(v).toLocaleString():'—';const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;const STATE={{users:[],page:1,pageSize:25,query:''}};function table(headers,rows){{if(!rows.length)return'<div class="empty">No data yet.</div>';return`<table><thead><tr>${{headers.map(h=>`<th>${{h}}</th>`).join('')}}</tr></thead><tbody>${{rows.join('')}}</tbody></table>`}}function td(text,cls=''){{const d=document.createElement('div');d.textContent=esc(text);return`<td class="${{cls}}">${{d.innerHTML}}</td>`}}function animateNumber(el,target){{const end=Number(target);if(!Number.isFinite(end)||reduceMotion){{el.textContent=target;return}}const start=performance.now(),duration=520;function tick(now){{const p=Math.min(1,(now-start)/duration);const eased=1-Math.pow(1-p,3);el.textContent=Math.round(end*eased).toLocaleString();if(p<1)requestAnimationFrame(tick);else el.textContent=end.toLocaleString()}}requestAnimationFrame(tick)}}function toast(message){{const el=document.getElementById('toast');el.textContent=message;el.classList.add('show');clearTimeout(window.__toastTimer);window.__toastTimer=setTimeout(()=>el.classList.remove('show'),1800)}}function metricCards(s){{const items=[['Users',s.users],['Active · 7d',s.active_users_7d],['AI requests · today',s.requests_today],['Open tickets',s.open_tickets]];const root=document.getElementById('metrics');root.innerHTML=items.map(([a,b],i)=>`<div class="card" style="animation-delay:${{i*45}}ms"><div class="label">${{a}}</div><div class="metric" data-value="${{esc(b)}}">0</div></div>`).join('');root.querySelectorAll('.metric').forEach(el=>animateNumber(el,el.dataset.value))}}function filteredUsers(){{const q=STATE.query.trim().toLowerCase();if(!q)return STATE.users;return STATE.users.filter(u=>[u.student_name,u.display_name,u.university,u.username,u.telegram_id].some(v=>String(v??'').toLowerCase().includes(q)))}}function renderUsers(){{const all=filteredUsers();const pages=Math.max(1,Math.ceil(all.length/STATE.pageSize));STATE.page=Math.min(STATE.page,pages);const start=(STATE.page-1)*STATE.pageSize;const rows=all.slice(start,start+STATE.pageSize);document.getElementById('users').innerHTML=table(['Name','Institution','Username','Telegram ID','Tier','Daily limit','Onboarded','Joined','Last seen'],rows.map(u=>`<tr>${{td(u.student_name||u.display_name||'Student')}}${{td(u.university||'—')}}${{td(u.username?'@'+u.username:'—')}}${{td(u.telegram_id)}}<td><span class="badge ${{esc(u.access_tier)}}">${{esc(u.access_tier)}}</span></td>${{td(u.daily_limit)}}${{td(u.onboarding_completed?'Yes':'Pending')}}${{td(date(u.created_at))}}${{td(date(u.updated_at))}}</tr>`));document.getElementById('user-count').textContent=`${{all.length.toLocaleString()}} user${{all.length===1?'':'s'}}`;document.getElementById('user-page').textContent=`Page ${{STATE.page}} / ${{pages}}`;document.getElementById('user-prev').disabled=STATE.page<=1;document.getElementById('user-next').disabled=STATE.page>=pages}}function render(d){{metricCards(d.summary);const modes=Object.entries(d.mode_usage).sort((a,b)=>b[1]-a[1]);const max=Math.max(1,...modes.map(x=>x[1]));document.getElementById('modes').innerHTML=modes.length?modes.map(([m,n],i)=>`<div class="bar"><div>${{esc(m)}}</div><div class="track"><span class="barfill" style="width:${{Math.max(3,Math.round(n/max*100))}}%;animation-delay:${{120+i*55}}ms"></span></div><b>${{n}}</b></div>`).join(''):'<div class="empty">No mode usage yet.</div>';STATE.users=d.users||[];renderUsers();document.getElementById('activity').innerHTML=table(['When','Student','Telegram ID','Event','Mode','Detail','Institution'],(d.recent_activity||[]).map(a=>`<tr>${{td(date(a.created_at))}}${{td(a.display_name)}}${{td(a.telegram_id)}}<td><span class="badge">${{esc(a.event_type)}}</span></td>${{td(a.mode||'—')}}${{td(a.detail||'—','message')}}${{td(a.university||'—')}}</tr>`));document.getElementById('tickets').innerHTML=table(['Created','User','Message','Status'],d.tickets.map(t=>`<tr>${{td(date(t.created_at))}}${{td(t.display_name+(t.username?' · @'+t.username:''))}}${{td(t.message,'message')}}<td><select data-id="${{esc(t.id)}}"><option value="open" ${{t.status==='open'?'selected':''}}>Open</option><option value="in_progress" ${{t.status==='in_progress'?'selected':''}}>In Progress</option><option value="resolved" ${{t.status==='resolved'?'selected':''}}>Resolved</option></select></td></tr>`));document.querySelectorAll('select[data-id]').forEach(el=>el.addEventListener('change',async()=>{{el.disabled=true;el.classList.add('saving');const r=await fetch('/admin/api/tickets/'+encodeURIComponent(el.dataset.id),{{method:'PATCH',headers:{{'Content-Type':'application/json','X-BuildWise-CSRF':CSRF}},body:JSON.stringify({{status:el.value}})}});el.disabled=false;el.classList.remove('saving');if(!r.ok){{toast('Status update failed');await load(false);return}}el.classList.add('saved');setTimeout(()=>el.classList.remove('saved'),700);toast('Ticket status updated')}}))}}async function load(showToast=true){{const btn=document.getElementById('refresh');btn.disabled=true;btn.innerHTML='<span class="spin"></span>Refreshing';try{{const r=await fetch('/admin/api/dashboard',{{cache:'no-store'}});if(!r.ok)throw new Error('Could not load admin data');const d=await r.json();render(d);document.getElementById('stamp').textContent='Updated '+new Date().toLocaleTimeString([],{{hour:'2-digit',minute:'2-digit'}});if(showToast)toast('Dashboard refreshed')}}finally{{btn.disabled=false;btn.textContent='Refresh'}}}}document.getElementById('refresh').addEventListener('click',()=>load(true));document.getElementById('user-search').addEventListener('input',e=>{{STATE.query=e.target.value;STATE.page=1;renderUsers()}});document.getElementById('user-page-size').addEventListener('change',e=>{{STATE.pageSize=Number(e.target.value)||25;STATE.page=1;renderUsers()}});document.getElementById('user-prev').addEventListener('click',()=>{{STATE.page=Math.max(1,STATE.page-1);renderUsers()}});document.getElementById('user-next').addEventListener('click',()=>{{STATE.page+=1;renderUsers()}});load(false).catch(e=>{{document.getElementById('metrics').innerHTML='<div class="empty">'+esc(e.message)+'</div>';toast(e.message)}});
</script></body></html>'''
