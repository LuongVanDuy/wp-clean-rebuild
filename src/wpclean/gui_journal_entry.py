from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse
import re

from . import gui_entry  # noqa: F401 - activates existing GUI patches
from . import gui_server as server
from . import gui_ui
from .gui_observability import error_payload
from .project_journal import append_activity, read_activity, reconcile_automatic_todos, set_todo_status


_BASE_PAYLOAD = server._project_payload
_BASE_RENDER = gui_ui.render_app
_BASE_RUN_PIPELINE = server._run_pipeline
_BASE_POST = server.GuiHandler.do_POST


_JOURNAL_CSS = r'''<style id="wpclean-journal-style">
.detail-tabs{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:73px;z-index:3}.detail-tab{border:0;background:transparent;padding:14px 14px 12px;color:#64748b;font-size:14px;font-weight:600;border-bottom:2px solid transparent}.detail-tab:hover{color:#25344a}.detail-tab.active{color:#1d4ed8;border-bottom-color:#2563eb}.tab-count{display:inline-flex;min-width:20px;height:20px;padding:0 6px;margin-left:5px;align-items:center;justify-content:center;border-radius:10px;background:#eef2f7;color:#526174;font-size:11px}.detail-tab.active .tab-count{background:#e7efff;color:#1d4ed8}
.history-panel,.todo-panel{padding:22px 24px 36px}.history-head,.todo-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:16px}.history-head h3,.todo-head h3{margin:0;font-size:20px;font-weight:600}.history-head p,.todo-head p{margin:4px 0 0;color:var(--muted);font-size:14px}.history-day{margin:20px 0 10px;font-size:15px;font-weight:600;color:#334155}.history-list{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}.history-row{display:grid;grid-template-columns:90px 130px 1fr;gap:14px;padding:11px 13px;border-bottom:1px solid #edf0f4;align-items:start}.history-row:last-child{border-bottom:0}.history-time{font:13px/1.5 Consolas,monospace;color:#64748b}.history-stage{font-size:13px;color:#475569;font-weight:500}.history-msg{font-size:14px;line-height:1.55;color:#263445;word-break:break-word}.history-code{display:inline-flex;margin:0 7px 4px 0;border-radius:4px;background:#fee2e2;color:#991b1b;padding:3px 6px;font:600 11px/1.2 Consolas,monospace}.history-recovery{display:block;margin-top:5px;color:#7a3c43}.history-technical summary{cursor:pointer;margin-top:5px;color:#64748b;font-size:12px}.history-technical pre{max-height:180px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#111827;color:#cbd5e1;padding:8px;border-radius:5px;font:11px/1.5 Consolas,monospace}.history-error .history-msg{color:#a82e3b}.history-empty,.todo-empty{padding:36px;text-align:center;border:1px dashed #cfd7e2;border-radius:9px;color:#718096;font-size:14px;background:#fafbfc}
.todo-summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.todo-chip{border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px;color:#536174;background:#fff}.todo-list{display:grid;gap:10px}.todo-card{border:1px solid var(--line);border-radius:9px;padding:14px;background:#fff;display:grid;grid-template-columns:28px 1fr auto;gap:11px;align-items:start}.todo-card.pending{border-color:#e5c98d;background:#fffdf8}.todo-card.done{opacity:.78}.todo-icon{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;background:#fff3d6;color:#9a6500}.todo-card.done .todo-icon{background:#e8f7f0;color:#15805d}.todo-title{font-size:15px;font-weight:600;margin:1px 0 4px}.todo-detail{font-size:13px;color:#617084;line-height:1.55;white-space:pre-wrap;word-break:break-word}.todo-meta{font-size:12px;color:#8995a5;margin-top:7px}.todo-toggle{white-space:nowrap}
@media(max-width:900px){.history-row{grid-template-columns:72px 1fr}.history-stage{display:none}.todo-card{grid-template-columns:28px 1fr}.todo-toggle{grid-column:2}.detail-tabs{top:64px;overflow:auto;padding-left:14px;padding-right:14px}}
</style>'''


_CODER_CSS = r'''<style id="wpclean-coder-style">
:root{--bg:#080d17;--panel:#0f1726;--text:#e6edf7;--muted:#8b9bb0;--line:#263349;--soft:#111b2c;--brand:#22c55e;--brand-dark:#16a34a;--green:#4ade80;--green-bg:#10281d;--yellow:#fbbf24;--yellow-bg:#2b2211;--red:#fb7185;--red-bg:#30151d;--nav:#070b13;--radius:7px;--mono:"Cascadia Code","JetBrains Mono",Consolas,"Courier New",monospace}
html{color-scheme:dark}html,body{background:var(--bg);color:var(--text);font-family:Inter,"Segoe UI",Roboto,Arial,sans-serif}body{background-image:linear-gradient(rgba(56,189,248,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.018) 1px,transparent 1px);background-size:24px 24px}
.layout{display:block!important}.sidebar{display:none!important}.brandmark{border:1px solid #295a3c;border-radius:6px;background:#10281d;color:#4ade80;font-family:var(--mono)}.brand strong,.top-title,.page-head h1,.drawer-title h2,.modalhead h2{color:#f8fafc}.brand span,.side-note{color:#708198}.navbtn{border:1px solid transparent;border-radius:5px;font-family:var(--mono);font-size:12px}.navbtn:hover,.navbtn.active{border-color:#284036;background:#10251b;color:#86efac}.dot{background:#4ade80;box-shadow:0 0 10px rgba(74,222,128,.55)}
.topbar{background:rgba(9,15,26,.96);border-color:var(--line);backdrop-filter:blur(10px)}.top-title{font-family:var(--mono);letter-spacing:.02em}.content{max-width:1800px}.page-head p,.project-name p,.current-step,.drawer-title p,.modalhead p,.job p,.action-card p,.foot-danger p{color:var(--muted)}
.summary,.panel,.drawer,.drawer-head,.detail-tabs,.modalbox,.connection,.steps,.job,.jobmetric,.history-list,.todo-chip,.todo-card,.check{background:var(--panel);border-color:var(--line)}.summary,.panel,.connection,.steps,.job,.history-list,.todo-card,.modalbox{box-shadow:0 14px 35px rgba(0,0,0,.16)}.summary-item span,.project-header,.section-title,.field label,.jobmetric span{color:#7f91a8;font-family:var(--mono);letter-spacing:.06em}.summary-item b,.project-name h3,.kv b,.step label,.job h3,.jobmetric b,.todo-title{color:#eaf1fb}.summary-sep{background:var(--line)}
.panel-head,.project-header,.project-row,.drawer-head,.detail-tabs,.step,.connection-actions,.foot-danger,.history-row{border-color:var(--line)}.panel-head,.project-header{background:#0c1422}.project-row{background:transparent;transition:background .16s,border-color .16s}.project-row:hover{background:#111d2e}.site-icon{border:1px solid #2a455e;border-radius:5px;background:#102235;color:#7dd3fc;font-family:var(--mono)}.open-btn{border-color:#34445b;background:#111c2d;color:#b9c7d9;font-family:var(--mono)}
.search,.confirm-input,.field input,.field select{border-color:#34445b;background:#0a1220;color:#e6edf7}.search::placeholder,.confirm-input::placeholder,.field input::placeholder{color:#5f7086}.search:focus,.confirm-input:focus,.field input:focus,.field select:focus{border-color:#22c55e;box-shadow:0 0 0 3px rgba(34,197,94,.13)}
.btn{border-radius:5px;font-family:var(--mono);letter-spacing:-.01em}.btn-primary{background:#22c55e;color:#041109;border-color:#4ade80}.btn-primary:hover{background:#4ade80}.btn-light{background:#111b2b;border-color:#34445b;color:#c9d5e5}.btn-light:hover{background:#182438}.btn-success{background:#10281d;border-color:#295a3c;color:#86efac}.btn-warning{background:#2b2211;border-color:#67501b;color:#fcd34d}.btn-danger{background:#30151d;border-color:#71303e;color:#fda4af}.xbtn{border-color:#34445b;background:#101a2a;color:#9fb0c5;border-radius:5px}.xbtn:hover{background:#182438;color:#fff}
.status{border:1px solid transparent;font-family:var(--mono)}.status-green{background:#10281d;border-color:#295a3c;color:#86efac}.status-blue{background:#102235;border-color:#2a455e;color:#7dd3fc}.status-yellow{background:#2b2211;border-color:#67501b;color:#fcd34d}.status-red{background:#30151d;border-color:#71303e;color:#fda4af}.progress,.jobbar{background:#1d293a}.progress span,.jobbar span{background:#22c55e}.step.active{background:#102235}.stepdot{background:#1b2738;color:#91a2b7}.step.done .stepdot{background:#10281d;color:#86efac}.step.active .stepdot{background:#163451;color:#7dd3fc}.step small{color:#78899f}
.action-card{border-color:#2d4158;background:#0e1928}.action-card.warn{border-color:#67501b;background:#211b10}.action-card.danger{border-color:#71303e;background:#24121a}.check{background:#0b1422}.check input{accent-color:#22c55e}.job{background:#0c1524}.jobmetric{background:#0a1220}.job-health{color:#9cacbe}.jobcur{color:#91a2b7;font-family:var(--mono)}.technical-details pre,.history-technical pre{border:1px solid #2b3950;background:#080e19;color:#c7d2e3}.error-summary,.errorbox{border-color:#71303e;background:#24121a;color:#fda4af}.error-summary h4{color:#fecdd3}.error-recovery{border-color:#5b2934;color:#fda4af}
.terminal-panel{border-color:#2a3a51;border-radius:7px;background:#080e19;box-shadow:0 16px 40px rgba(0,0,0,.3)}.terminal-head{border-color:#2a3a51;background:#0c1524}.terminal-output{background:#080e19;color:#c7d2e3;font-family:var(--mono)}.terminal-title,.terminal-copy,.terminal-meta{font-family:var(--mono)}.terminal-dot{background:#4ade80;box-shadow:0 0 12px rgba(74,222,128,.55)}.terminal-copy{border-color:#34445b;background:#111b2b;color:#c9d5e5}.terminal-copy:hover{background:#182438}
.detail-tab{color:#8293a9;font-family:var(--mono)}.detail-tab:hover{color:#c9d5e5}.detail-tab.active{color:#4ade80;border-bottom-color:#22c55e}.tab-count{background:#1b2738;color:#a8b6c8}.detail-tab.active .tab-count{background:#143522;color:#86efac}.history-day{color:#b8c6d8;font-family:var(--mono)}.history-list,.history-row{background:var(--panel)}.history-time,.history-stage,.todo-meta{color:#8293a9}.history-msg,.todo-detail{color:#c5d1e0}.history-empty,.todo-empty{border-color:#34445b;background:#0b1422;color:#8293a9}.todo-card.pending{border-color:#67501b;background:#211b10}.todo-icon{background:#2b2211;color:#fcd34d}.todo-card.done .todo-icon{background:#10281d;color:#86efac}.todo-chip{color:#aab8c9}
.overlay{background:rgba(2,6,12,.72);backdrop-filter:blur(2px)}.modalbox{width:min(760px,94vw);border-radius:8px;padding:22px 24px}.modalhead{padding-bottom:15px;border-bottom:1px solid var(--line);margin-bottom:18px}.formgrid{grid-template-columns:repeat(6,minmax(0,1fr));gap:14px 16px;align-items:start}.field{display:flex;flex-direction:column;grid-column:span 3;gap:6px;align-self:start}.field.third{grid-column:span 2}.field.full{grid-column:1/-1}.field label{font-size:11px;text-transform:uppercase}.field input,.field select{width:100%;height:44px;min-height:44px;padding:0 12px;border-radius:5px;font-family:var(--mono);font-size:14px}.hint{color:#74859b;font-family:var(--mono);font-size:11px}.modalactions{padding-top:16px;border-top:1px solid var(--line)}.toast{border:1px solid #34445b;background:#111b2b;color:#e6edf7;font-family:var(--mono)}.toast.err{border-color:#71303e;background:#30151d;color:#fecdd3}
.system-health{color:#91a2b7;font-family:var(--mono)}.system-health-dot{background:#4ade80;box-shadow:0 0 0 3px #143522,0 0 12px rgba(74,222,128,.35)}
.drawer{top:16px;right:16px;bottom:16px;width:min(1500px,calc(100vw - 32px));border:1px solid #2b3950;border-radius:8px;background:#0a111e;box-shadow:-18px 0 55px rgba(0,0,0,.38)}.drawer-head{min-height:72px;padding:14px 20px;background:#0c1524}.drawer-title{align-items:center}.drawer-title-main{min-width:0}.drawer-project-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:7px}.project-token,.workflow-token{display:inline-flex;align-items:center;min-height:24px;border:1px solid #34445b;border-radius:4px;background:#0a1220;padding:3px 7px;color:#9fb0c5;font:11px/1.2 var(--mono)}.detail-tabs{top:72px;background:#0c1524}.drawer-body{padding:18px 20px 24px}.detail-layout{grid-template-columns:minmax(470px,.82fr) minmax(650px,1.18fr);gap:18px}.detail-left,.detail-right{min-width:0}.detail-left>.section{margin-bottom:16px}.detail-right{top:104px}.terminal-head{min-height:58px}.terminal-tools{display:flex;align-items:center;gap:7px}.terminal-output{height:calc(100vh - 188px);min-height:560px}.foot-danger{justify-content:flex-end;margin-top:16px;padding-top:14px}.action-card.confirmation-reveal{border-color:#fbbf24;box-shadow:0 0 0 3px rgba(251,191,36,.16),0 12px 28px rgba(0,0,0,.2);outline:0}
.steps,.plugin-choice-list{scrollbar-color:#3b4a61 #0a1220}.plugin-choice-summary{color:#91a2b7;font-family:var(--mono)}
.project-todo-badge{border-color:#67501b;background:#2b2211;color:#fcd34d;font-family:var(--mono)}.project-todo-badge.empty{border-color:#34445b;background:#111b2b;color:#91a2b7}
@media(max-width:1100px){.formgrid{grid-template-columns:1fr 1fr}.field,.field.third{grid-column:auto}.field.full{grid-column:1/-1}}
@media(max-width:1180px){.drawer{top:0;right:0;bottom:0;width:min(980px,100vw);border-radius:0}.detail-layout{grid-template-columns:1fr}.detail-right{position:relative;top:auto}.terminal-output{height:440px;min-height:360px}}
@media(max-width:720px){.formgrid{grid-template-columns:1fr}.field,.field.third,.field.full{grid-column:1}.modalbox{padding:18px}.terminal-tools{gap:4px}.terminal-copy{padding:6px 8px}}
</style>'''


_JOURNAL_JS = r'''
state.detailTab=state.detailTab||'progress';state.currentProject=null;state.confirmationRevealKey=state.confirmationRevealKey||'';state.renderedProject=state.renderedProject||'';state.renderedTab=state.renderedTab||'';state.renderedDecisionKey=state.renderedDecisionKey||'';state.renderedJobKey=state.renderedJobKey||'';
function confirmationKey(p){const j=p.job||{},d=j.decision||null;if(j.status!=='needs-action'||!d)return '';return [p.name||'',j.stage||'',d.type||'',j.message||''].join('|')}
function decisionStateKey(p){const j=p.job||{},d=j.decision||null;if(j.status==='needs-action'&&d)return JSON.stringify(['decision',j.stage||'',d]);if(j.status==='error')return JSON.stringify(['error',j.errorCode||'',j.error||'',j.retryable]);if(p.completed)return JSON.stringify(['complete',Number(p.pendingTodoCount||0)]);return JSON.stringify([j.status||'idle',p.nextStage||'',p.nextLabel||''])}
function jobStateKey(p){const j=p.job||{};return JSON.stringify([j.status||'',j.stage||'',j.sequence||0,j.health||'',j.errorCode||''])}
function revealConfirmation(){const cards=[...document.querySelectorAll('#drawerContent .detail-left .action-card')],target=cards[cards.length-1];if(!target)return;target.classList.add('confirmation-reveal');target.setAttribute('tabindex','-1');target.setAttribute('role','region');target.setAttribute('aria-label','Bước cần xác nhận');const reduced=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;target.scrollIntoView({behavior:reduced?'auto':'smooth',block:'center',inline:'nearest'});try{target.focus({preventScroll:true})}catch{target.focus()}setTimeout(()=>target.classList.remove('confirmation-reveal'),1800)}
const openProjectBeforeConfirmationReveal=openProject;openProject=async function(encoded){state.confirmationRevealKey='';return openProjectBeforeConfirmationReveal(encoded)}
function fmtHistoryDay(ts){try{return new Date(ts).toLocaleDateString('vi-VN',{weekday:'long',day:'2-digit',month:'2-digit',year:'numeric'})}catch{return String(ts||'').slice(0,10)}}
function fmtHistoryTime(ts){try{return new Date(ts).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch{return String(ts||'').slice(11,19)}}
function historyHtml(p){const items=Array.isArray(p.activity)?p.activity:[];if(!items.length)return `<div class="history-panel"><div class="history-head"><div><h3>Lịch sử dự án</h3><p>Log sẽ được lưu lại kể cả khi đóng ứng dụng hoặc khởi động lại máy.</p></div></div><div class="history-empty">Chưa có lịch sử xử lý được lưu.</div></div>`;let out='',last='';for(const item of items){const day=String(item.timestamp||'').slice(0,10);if(day!==last){if(last)out+='</div>';out+=`<div class="history-day">${esc(fmtHistoryDay(item.timestamp))}</div><div class="history-list">`;last=day}const code=item.code?`<span class="history-code">${esc(item.code)}</span>`:'',recovery=item.recovery?`<span class="history-recovery"><b>Cách xử lý:</b> ${esc(item.recovery)}</span>`:'',technical=item.technical?`<details class="history-technical"><summary>Chi tiết kỹ thuật</summary><pre>${esc(item.technical)}</pre></details>`:'';out+=`<div class="history-row ${item.level==='error'?'history-error':''}"><div class="history-time">${esc(fmtHistoryTime(item.timestamp))}</div><div class="history-stage">${esc(item.stage||'—')}</div><div class="history-msg">${code}${esc(item.title||item.message||'')}${item.title&&item.message?`<br>${esc(item.message)}`:''}${recovery}${technical}</div></div>`}if(last)out+='</div>';return `<div class="history-panel"><div class="history-head"><div><h3>Lịch sử dự án</h3><p>${items.length} dòng gần nhất · lưu tại reports/${esc(p.host)}/activity-log.jsonl</p></div></div>${out}</div>`}
function todoHtml(p){const todos=Array.isArray(p.todos)?p.todos:[],pending=todos.filter(x=>x.status!=='done'),done=todos.filter(x=>x.status==='done');const kind=t=>t.kind==='security'?'Bảo mật':t.kind||'',card=t=>`<div class="todo-card ${t.status==='done'?'done':'pending'}"><div class="todo-icon">${t.status==='done'?'✓':'!'}</div><div><div class="todo-title">${esc(t.title||'')}</div><div class="todo-detail">${esc(t.detail||'')}</div><div class="todo-meta">${esc(kind(t))} · ${t.status==='done'?'Hoàn tất':'Đang chờ'}${t.completed_at?' · '+esc(fmtHistoryDay(t.completed_at))+' '+esc(fmtHistoryTime(t.completed_at)):''}</div></div><button class="btn btn-light todo-toggle" onclick="toggleTodo('${esc(p.name)}','${esc(t.id)}',${t.status==='done'?'false':'true'})">${t.status==='done'?'Mở lại':'Đánh dấu hoàn tất'}</button></div>`;return `<div class="todo-panel"><div class="todo-head"><div><h3>Việc cần làm</h3><p>Các việc còn lại về plugin, theme, bảo mật và kỹ thuật được lưu tại đây.</p></div></div><div class="todo-summary"><div class="todo-chip">Đang chờ: <b>${pending.length}</b></div><div class="todo-chip">Hoàn tất: <b>${done.length}</b></div></div>${todos.length?`<div class="todo-list">${pending.map(card).join('')}${done.map(card).join('')}</div>`:'<div class="todo-empty">Hiện không có việc cần xử lý thêm.</div>'}</div>`}
function projectExtrasHtml(p){return `${p.themeRepair?`<div class="section"><button class="btn btn-warning" onclick="openRepair('${esc(p.name)}')">Mở thư mục theme repair</button></div>`:''}${p.completed?`<div class="foot-danger"><button class="btn btn-danger" onclick="deleteProject('${esc(p.name)}')">Xóa dự án local</button></div>`:''}`}
function progressTabHtml(p){return `<div class="drawer-body"><div class="detail-layout"><div class="detail-left"><div class="section"><div class="section-head"><div class="section-title">Kết nối FTP</div></div><div id="connectionRegion">${connectionHtml(p)}</div></div><div class="section"><div class="section-head"><div class="section-title">Tiến độ xử lý</div></div><div id="workflowSteps" class="steps">${stepHtml(p)}</div><div id="jobRegion">${jobHtml(p)}</div><div id="decisionRegion">${decisionHtml(p)}</div></div><div id="projectExtras">${projectExtrasHtml(p)}</div></div><div class="detail-right">${terminalPanelHtml(p)}</div></div></div>`}
function detailTabs(p){const pending=Number(p.pendingTodoCount||0),history=(p.activity||[]).length;return `<div class="detail-tabs"><button class="detail-tab ${state.detailTab==='progress'?'active':''}" onclick="setDetailTab('progress')">Tiến độ</button><button class="detail-tab ${state.detailTab==='history'?'active':''}" onclick="setDetailTab('history')">Lịch sử <span id="historyTabCount" class="tab-count">${history}</span></button><button class="detail-tab ${state.detailTab==='todos'?'active':''}" onclick="setDetailTab('todos')">Việc cần làm <span id="todoTabCount" class="tab-count">${pending}</span></button></div>`}
function drawerMetaHtml(p){const pc=progressOf(p),si=statusInfo(p);return `<span class="project-token">${esc(p.name)}</span><span class="status ${si[0]}">${si[1]}</span><span class="workflow-token">${pc}% workflow</span>${p.pendingTodoCount?`<span class="workflow-token">${p.pendingTodoCount} việc chờ</span>`:''}`}
function replaceRegion(id,html){const el=qs('#'+id);if(!el)return false;if(el.innerHTML!==html)el.innerHTML=html;return true}
function patchDrawerChrome(p){const meta=qs('#drawerContent .drawer-project-meta');if(meta){const html=drawerMetaHtml(p);if(meta.innerHTML!==html)meta.innerHTML=html}const history=qs('#historyTabCount'),todos=qs('#todoTabCount');if(history)history.textContent=String((p.activity||[]).length);if(todos)todos.textContent=String(Number(p.pendingTodoCount||0))}
function patchTerminalPanel(p){const out=qs('#terminalOutput');if(!out)return;const j=p.job||{},logs=Array.isArray(j.logs)?j.logs:[],last=logs.length?String(logs[logs.length-1]):'',logKey=JSON.stringify([logs.length,last]),oldTop=out.scrollTop;if(out.dataset.logKey!==logKey){out.innerHTML=logs.length?logs.map(logLineHtml).join(''):'Chưa có log trong phiên GUI này. Nhấn Tiếp tục để bắt đầu xử lý.';out.dataset.logKey=logKey;out.classList.toggle('terminal-empty',!logs.length);if(state.logFollow)out.scrollTop=out.scrollHeight;else out.scrollTop=oldTop}const status=j.healthLabel||(j.status==='running'?'Đang chạy':j.status==='error'?'Có lỗi':j.status==='needs-action'?'Chờ xác nhận':j.status==='paused'?'Tạm dừng':j.status==='success'?'Hoàn tất':'Sẵn sàng'),signal=j.updatedAt?` · ${formatSignal(j.idleSeconds)}`:'',meta=qs('#drawerContent .terminal-meta');if(meta)meta.textContent=`${status}${signal} · ${logs.length} dòng`}
function patchProgressDrawer(p,reveal){if(!replaceRegion('connectionRegion',connectionHtml(p))||!replaceRegion('workflowSteps',stepHtml(p)))return false;const nextJobKey=jobStateKey(p),j=p.job||{};if(j.status==='running'||nextJobKey!==state.renderedJobKey)replaceRegion('jobRegion',jobHtml(p));state.renderedJobKey=nextJobKey;const nextDecisionKey=decisionStateKey(p);if(nextDecisionKey!==state.renderedDecisionKey){replaceRegion('decisionRegion',decisionHtml(p));state.renderedDecisionKey=nextDecisionKey}replaceRegion('projectExtras',projectExtrasHtml(p));patchDrawerChrome(p);patchTerminalPanel(p);if(reveal)requestAnimationFrame(revealConfirmation);return true}
function setDetailTab(tab){state.detailTab=tab;const p=state.currentProject;if(p)renderDrawer(p,true)}
renderDrawer=function(p,force=false){const key=confirmationKey(p),reveal=!!key&&key!==state.confirmationRevealKey;if(reveal)state.detailTab='progress';if(!key&&state.confirmationRevealKey.startsWith((p.name||'')+'|'))state.confirmationRevealKey='';const sameView=!force&&state.renderedProject===p.name&&state.renderedTab===state.detailTab;state.selected=p.name;state.currentProject=p;if(sameView&&state.detailTab==='progress'&&patchProgressDrawer(p,reveal)){if(reveal)state.confirmationRevealKey=key;return}if(sameView&&(state.detailTab==='history'||state.detailTab==='todos')){patchDrawerChrome(p);return}const si=statusInfo(p),body=state.detailTab==='history'?historyHtml(p):state.detailTab==='todos'?todoHtml(p):progressTabHtml(p);qs('#drawerContent').innerHTML=`<div class="drawer-head"><div class="drawer-title"><div class="drawer-title-main"><h2>${esc(p.host)}</h2><div class="drawer-project-meta">${drawerMetaHtml(p)}</div></div><button class="xbtn" type="button" aria-label="Đóng chi tiết dự án" onclick="closePanels()">×</button></div></div>${detailTabs(p)}${body}`;state.renderedProject=p.name;state.renderedTab=state.detailTab;state.renderedDecisionKey=decisionStateKey(p);state.renderedJobKey=jobStateKey(p);if(reveal){state.confirmationRevealKey=key;requestAnimationFrame(revealConfirmation)}else if(state.detailTab==='progress'&&state.logFollow)setTimeout(scrollTerminalBottom,0)}
async function toggleTodo(name,id,completed){try{const p=await api('/api/projects/'+encodeURIComponent(name)+'/todos/'+encodeURIComponent(id),{method:'POST',body:JSON.stringify({completed:!!completed})});state.currentProject=p;renderDrawer(p,true);toast(completed?'Đã đánh dấu hoàn tất':'Đã mở lại việc cần làm')}catch(e){toast(e.message,true)}}
'''


def _render_app(token: str) -> str:
    html = _BASE_RENDER(token)
    html = html.replace("</head>", _JOURNAL_CSS + "\n" + _CODER_CSS + "\n</head>", 1)
    return html.replace("</script>\n</body>", _JOURNAL_JS + "\n</script>\n</body>", 1)


def _journal_dir(name: str):
    _profile_path, profile, _paths = server._profile_and_paths(name)
    return server.REPORTS_DIR / profile.host, profile


def _run_pipeline(name: str, options: dict[str, Any], job) -> None:
    report_dir, profile = _journal_dir(name)
    job._journal_dir = report_dir
    job._journal_secrets = tuple(value for value in (profile.password or "",) if value)
    job._journal_session = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    job.log("Bắt đầu phiên xử lý")
    try:
        _BASE_RUN_PIPELINE(name, options, job)
    finally:
        job.log(f"Kết thúc phiên · trạng thái {job.status}")


def _project_payload(name: str) -> dict[str, Any]:
    payload = _BASE_PAYLOAD(name)
    report_dir, profile = _journal_dir(name)
    execution_path = report_dir / "rebuild-execute.json"
    operator_path = report_dir / "operator-state.json"
    execution = server._json_read(execution_path)
    operator_state = server._json_read(operator_path)
    profile_raw = server._json_read(server._safe_project_path(name))
    initial_password_fingerprint = str(profile_raw.get("initialFtpPasswordFingerprint") or "")
    current_password_fingerprint = server._secret_fingerprint(profile.password or "")
    ftp_password_changed = bool(
        initial_password_fingerprint and initial_password_fingerprint != current_password_fingerprint
    )
    todos = reconcile_automatic_todos(
        report_dir,
        execution=execution,
        operator_state=operator_state,
        project_completed=bool(payload.get("completed")),
        ftp_password_changed=ftp_password_changed,
    )
    activity = read_activity(report_dir, limit=500)
    payload["activity"] = activity
    payload["todos"] = todos
    payload["pendingTodoCount"] = sum(1 for item in todos if item.get("status") != "done")
    payload["historyFile"] = str(report_dir / "activity-log.jsonl")
    payload["notesFile"] = str(report_dir / "project-notes.json")
    return payload


def _set_project_todo_status(name: str, identifier: str, *, completed: bool) -> dict[str, Any]:
    report_dir, _profile = _journal_dir(name)
    set_todo_status(report_dir, identifier, completed=completed)
    append_activity(
        report_dir,
        project=name,
        stage="notes",
        message=("Đánh dấu hoàn tất việc cần làm" if completed else "Mở lại việc cần làm") + f": {identifier}",
    )
    return _project_payload(name)


def _post_with_journal(self) -> None:
    path = (urlparse(self.path).path.rstrip("/") or "/")
    match = re.fullmatch(r"/api/projects/([^/]+)/todos/([^/]+)", path)
    if not match:
        _BASE_POST(self)
        return
    if not self._authorized():
        server._send_json(
            self,
            {
                "error": "Phiên GUI đã hết hiệu lực. Hãy tải lại trang.",
                "errorCode": "GUI-AUTH-001",
                "errorTitle": "Phiên GUI không hợp lệ",
                "recovery": "Refresh trang để nhận phiên local mới.",
                "retryable": True,
            },
            403,
        )
        return
    try:
        body = server._read_body(self)
        name = unquote(match.group(1))
        identifier = unquote(match.group(2))
        server._send_json(self, _set_project_todo_status(name, identifier, completed=bool(body.get("completed"))))
    except Exception as exc:
        server._send_json(self, error_payload(exc, stage="notes"), 400)


gui_ui.render_app = _render_app
server._run_pipeline = _run_pipeline
server._project_payload = _project_payload
server.GuiHandler.do_POST = _post_with_journal


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
