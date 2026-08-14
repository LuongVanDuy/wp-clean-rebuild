from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse
import re

from . import gui_entry as base_gui  # activates existing GUI patches
from . import gui_server as server
from . import gui_ui
from .project_journal import append_activity, read_activity, reconcile_automatic_todos, set_todo_status


_BASE_PAYLOAD = server._project_payload
_BASE_RENDER = gui_ui.render_app
_BASE_RUN_PIPELINE = server._run_pipeline
_BASE_JOB_LOG = server.GuiJob.log
_BASE_POST = server.GuiHandler.do_POST


_JOURNAL_CSS = r'''<style id="wpclean-journal-style">
.detail-tabs{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:73px;z-index:3}.detail-tab{border:0;background:transparent;padding:14px 14px 12px;color:#64748b;font-size:14px;font-weight:600;border-bottom:2px solid transparent}.detail-tab:hover{color:#25344a}.detail-tab.active{color:#1d4ed8;border-bottom-color:#2563eb}.tab-count{display:inline-flex;min-width:20px;height:20px;padding:0 6px;margin-left:5px;align-items:center;justify-content:center;border-radius:10px;background:#eef2f7;color:#526174;font-size:11px}.detail-tab.active .tab-count{background:#e7efff;color:#1d4ed8}
.history-panel,.todo-panel{padding:22px 24px 36px}.history-head,.todo-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:16px}.history-head h3,.todo-head h3{margin:0;font-size:20px;font-weight:600}.history-head p,.todo-head p{margin:4px 0 0;color:var(--muted);font-size:14px}.history-day{margin:20px 0 10px;font-size:15px;font-weight:600;color:#334155}.history-list{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}.history-row{display:grid;grid-template-columns:90px 130px 1fr;gap:14px;padding:11px 13px;border-bottom:1px solid #edf0f4;align-items:start}.history-row:last-child{border-bottom:0}.history-time{font:13px/1.5 Consolas,monospace;color:#64748b}.history-stage{font-size:13px;color:#475569;font-weight:500}.history-msg{font-size:14px;line-height:1.55;color:#263445;word-break:break-word}.history-error .history-msg{color:#a82e3b}.history-empty,.todo-empty{padding:36px;text-align:center;border:1px dashed #cfd7e2;border-radius:9px;color:#718096;font-size:14px;background:#fafbfc}
.todo-summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.todo-chip{border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:13px;color:#536174;background:#fff}.todo-list{display:grid;gap:10px}.todo-card{border:1px solid var(--line);border-radius:9px;padding:14px;background:#fff;display:grid;grid-template-columns:28px 1fr auto;gap:11px;align-items:start}.todo-card.pending{border-color:#e5c98d;background:#fffdf8}.todo-card.done{opacity:.78}.todo-icon{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;background:#fff3d6;color:#9a6500}.todo-card.done .todo-icon{background:#e8f7f0;color:#15805d}.todo-title{font-size:15px;font-weight:600;margin:1px 0 4px}.todo-detail{font-size:13px;color:#617084;line-height:1.55;white-space:pre-wrap;word-break:break-word}.todo-meta{font-size:12px;color:#8995a5;margin-top:7px}.todo-toggle{white-space:nowrap}.terminal-hint{padding:8px 14px;background:#172033;border-top:1px solid #2a3548;color:#8fa0b5;font-size:12px}
@media(max-width:900px){.history-row{grid-template-columns:72px 1fr}.history-stage{display:none}.todo-card{grid-template-columns:28px 1fr}.todo-toggle{grid-column:2}.detail-tabs{top:64px;overflow:auto;padding-left:14px;padding-right:14px}}
</style>'''


_JOURNAL_JS = r'''
state.detailTab=state.detailTab||'progress';state.currentProject=null;
function fmtHistoryDay(ts){try{return new Date(ts).toLocaleDateString('vi-VN',{weekday:'long',day:'2-digit',month:'2-digit',year:'numeric'})}catch{return String(ts||'').slice(0,10)}}
function fmtHistoryTime(ts){try{return new Date(ts).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch{return String(ts||'').slice(11,19)}}
function historyHtml(p){const items=Array.isArray(p.activity)?p.activity:[];if(!items.length)return `<div class="history-panel"><div class="history-head"><div><h3>Lịch sử dự án</h3><p>Log sẽ được lưu lại kể cả khi đóng ứng dụng hoặc khởi động lại máy.</p></div></div><div class="history-empty">Chưa có lịch sử xử lý được lưu.</div></div>`;let out='',last='';for(const item of items){const day=String(item.timestamp||'').slice(0,10);if(day!==last){out+=`<div class="history-day">${esc(fmtHistoryDay(item.timestamp))}</div><div class="history-list">`;if(last)out=out.replace(/<div class="history-list">([^]*?)<div class="history-day">$/,'<div class="history-list">$1</div><div class="history-day">');last=day}out+=`<div class="history-row ${item.level==='error'?'history-error':''}"><div class="history-time">${esc(fmtHistoryTime(item.timestamp))}</div><div class="history-stage">${esc(item.stage||'—')}</div><div class="history-msg">${esc(item.message||'')}</div></div>`}if(out.includes('history-list'))out+='</div>';return `<div class="history-panel"><div class="history-head"><div><h3>Lịch sử dự án</h3><p>${items.length} dòng gần nhất · lưu tại reports/${esc(p.host)}/activity-log.jsonl</p></div></div>${out}</div>`}
function todoHtml(p){const todos=Array.isArray(p.todos)?p.todos:[],pending=todos.filter(x=>x.status!=='done'),done=todos.filter(x=>x.status==='done');const card=t=>`<div class="todo-card ${t.status==='done'?'done':'pending'}"><div class="todo-icon">${t.status==='done'?'✓':'!'}</div><div><div class="todo-title">${esc(t.title||'')}</div><div class="todo-detail">${esc(t.detail||'')}</div><div class="todo-meta">${esc(t.kind||'')} · ${t.status==='done'?'Hoàn tất':'Đang chờ'}${t.completed_at?' · '+esc(fmtHistoryDay(t.completed_at))+' '+esc(fmtHistoryTime(t.completed_at)):''}</div></div><button class="btn btn-light todo-toggle" onclick="toggleTodo('${esc(p.name)}','${esc(t.id)}',${t.status==='done'?'false':'true'})">${t.status==='done'?'Mở lại':'Đánh dấu hoàn tất'}</button></div>`;return `<div class="todo-panel"><div class="todo-head"><div><h3>Việc cần làm</h3><p>Plugin/theme ngoài nguồn tự động và các việc kỹ thuật sẽ được giữ lại qua nhiều ngày.</p></div></div><div class="todo-summary"><div class="todo-chip">Đang chờ: <b>${pending.length}</b></div><div class="todo-chip">Hoàn tất: <b>${done.length}</b></div></div>${todos.length?`<div class="todo-list">${pending.map(card).join('')}${done.map(card).join('')}</div>`:'<div class="todo-empty">Hiện không có việc cần xử lý thêm.</div>'}</div>`}
function progressTabHtml(p){return `<div class="drawer-body"><div class="detail-layout"><div class="detail-left"><div class="section"><div class="section-head"><div class="section-title">Kết nối FTP</div></div>${connectionHtml(p)}</div><div class="section"><div class="section-head"><div class="section-title">Tiến độ xử lý</div></div><div class="steps">${stepHtml(p)}</div>${jobHtml(p)}${decisionHtml(p)}</div>${p.themeRepair?`<div class="section"><button class="btn btn-warning" onclick="openRepair('${esc(p.name)}')">Mở thư mục theme repair</button></div>`:''}<div class="foot-danger"><p>Xóa dự án chỉ xóa dữ liệu local, không đụng hosting.</p>${p.completed?`<button class="btn btn-danger" onclick="deleteProject('${esc(p.name)}')">Xóa dự án local</button>`:''}</div></div><div class="detail-right">${terminalPanelHtml(p)}<div class="terminal-hint">Log phiên hiện tại ở đây. Log các phiên cũ nằm trong tab Lịch sử.</div></div></div></div>`}
function detailTabs(p){const pending=Number(p.pendingTodoCount||0),history=(p.activity||[]).length;return `<div class="detail-tabs"><button class="detail-tab ${state.detailTab==='progress'?'active':''}" onclick="setDetailTab('progress')">Tiến độ</button><button class="detail-tab ${state.detailTab==='history'?'active':''}" onclick="setDetailTab('history')">Lịch sử <span class="tab-count">${history}</span></button><button class="detail-tab ${state.detailTab==='todos'?'active':''}" onclick="setDetailTab('todos')">Việc cần làm <span class="tab-count">${pending}</span></button></div>`}
function setDetailTab(tab){state.detailTab=tab;const p=state.currentProject;if(p)renderDrawer(p)}
renderDrawer=function(p){state.selected=p.name;state.currentProject=p;const pc=progressOf(p);let body=state.detailTab==='history'?historyHtml(p):state.detailTab==='todos'?todoHtml(p):progressTabHtml(p);qs('#drawerContent').innerHTML=`<div class="drawer-head"><div class="drawer-title"><div><h2>${esc(p.host)}</h2><p>${esc(p.name)} · ${pc}% hoàn tất${p.pendingTodoCount?` · ${p.pendingTodoCount} việc đang chờ`:''}</p></div><button class="xbtn" onclick="closePanels()">×</button></div></div>${detailTabs(p)}${body}`;if(state.detailTab==='progress')setTimeout(()=>{const out=qs('#terminalOutput');if(out)out.scrollTop=out.scrollHeight},0)}
async function toggleTodo(name,id,completed){try{const p=await api('/api/projects/'+encodeURIComponent(name)+'/todos/'+encodeURIComponent(id),{method:'POST',body:JSON.stringify({completed:!!completed})});state.currentProject=p;renderDrawer(p);toast(completed?'Đã đánh dấu hoàn tất':'Đã mở lại việc cần làm')}catch(e){toast(e.message,true)}}
'''


def _render_app(token: str) -> str:
    html = _BASE_RENDER(token)
    html = html.replace("</head>", _JOURNAL_CSS + "\n</head>", 1)
    return html.replace("</script>\n</body>", _JOURNAL_JS + "\n</script>\n</body>", 1)


def _journal_dir(name: str):
    _profile_path, profile, _paths = server._profile_and_paths(name)
    return server.REPORTS_DIR / profile.host, profile


def _persistent_log(self, text: str) -> None:
    _BASE_JOB_LOG(self, text)
    report_dir = getattr(self, "_journal_dir", None)
    if report_dir is None:
        return
    clean = base_gui._ANSI_RE.sub("", str(text)).replace("\r", "\n")
    secrets = getattr(self, "_journal_secrets", ())
    for raw in clean.splitlines():
        line = raw.strip()
        if line:
            append_activity(
                report_dir,
                project=self.project,
                stage=self.stage,
                level="error" if line.startswith("ERROR |") or "Traceback" in line else "info",
                session_id=getattr(self, "_journal_session", ""),
                message=line,
                secrets=secrets,
            )


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
    report_dir, _profile = _journal_dir(name)
    execution_path = report_dir / "rebuild-execute.json"
    operator_path = report_dir / "operator-state.json"
    execution = server._json_read(execution_path)
    operator_state = server._json_read(operator_path)
    todos = reconcile_automatic_todos(report_dir, execution=execution, operator_state=operator_state)
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
        server._send_json(self, {"error": "Phiên GUI không hợp lệ. Hãy refresh trang."}, 403)
        return
    try:
        body = server._read_body(self)
        name = unquote(match.group(1))
        identifier = unquote(match.group(2))
        server._send_json(self, _set_project_todo_status(name, identifier, completed=bool(body.get("completed"))))
    except Exception as exc:
        server._send_json(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)


gui_ui.render_app = _render_app
server.GuiJob.log = _persistent_log
server._run_pipeline = _run_pipeline
server._project_payload = _project_payload
server.GuiHandler.do_POST = _post_with_journal


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
