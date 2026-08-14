from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
import re
import threading
import traceback

from . import gui_journal_entry as base_gui  # activates GUI + persistent journal patches
from . import gui_server as server
from . import gui_ui
from .fresh_install import FreshInstallRequest, run_fresh_install


_BASE_RENDER = gui_ui.render_app
_BASE_GET = server.GuiHandler.do_GET
_BASE_POST = server.GuiHandler.do_POST
FRESH_JOBS: dict[str, "FreshGuiJob"] = {}
FRESH_LOCK = threading.Lock()


@dataclass
class FreshGuiJob:
    id: str
    project: str
    status: str = "running"
    phase: str = ""
    message: str = "Đang chuẩn bị"
    percent: int = 0
    current: str = ""
    error: str = ""
    logs: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None

    def log(self, text: str, secrets: tuple[str, ...] = ()) -> None:
        line = str(text).strip()
        for secret in secrets:
            if secret and len(secret) >= 3:
                line = line.replace(secret, "***")
        if not line:
            return
        self.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "percent": self.percent,
            "current": self.current,
            "error": self.error,
            "logs": self.logs[-200:],
            "report": self.report,
        }


_FRESH_CSS = r'''<style id="wpclean-fresh-install-style">
.fresh-modal .modalbox{width:min(1040px,97vw);padding:0;overflow:hidden}.fresh-head{padding:20px 24px 17px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:flex-start}.fresh-head h2{margin:0;font-size:25px;font-weight:600}.fresh-head p{margin:5px 0 0;color:var(--muted);font-size:15px}.fresh-body{padding:20px 24px 24px;max-height:calc(94vh - 78px);overflow:auto}.fresh-section{border:1px solid var(--line);border-radius:9px;padding:16px;margin-bottom:14px;background:#fff}.fresh-section h3{font-size:17px;font-weight:600;margin:0 0 12px}.fresh-section p{font-size:14px;color:var(--muted);margin:-5px 0 12px}.fresh-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 14px}.fresh-grid .field label{font-size:14px}.fresh-grid .field input,.fresh-grid .field select{font-size:15px;padding:10px 11px}.fresh-full{grid-column:1/-1}.fresh-db-admin{display:none}.fresh-db-admin.show{display:grid}.fresh-wipe{border-color:#edc9cf;background:#fffafa}.fresh-check{display:flex;gap:9px;align-items:flex-start;font-size:14px}.fresh-check input{margin-top:4px}.fresh-confirm{display:none;margin-top:12px}.fresh-confirm.show{display:grid}.fresh-actions{position:sticky;bottom:-24px;margin:18px -24px -24px;padding:14px 24px;background:#fff;border-top:1px solid var(--line);display:flex;justify-content:flex-end;gap:8px;z-index:2}.fresh-progress{display:none}.fresh-progress.show{display:block}.fresh-progress-top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}.fresh-progress-top h3{margin:0;font-size:17px;font-weight:600}.fresh-log{height:300px;overflow:auto;background:#0d1422;color:#cbd5e1;border-radius:8px;padding:13px 14px;font:13px/1.55 Consolas,"Cascadia Mono",monospace;white-space:pre-wrap;word-break:break-word}.fresh-bar{height:8px;background:#e7ebf1;border-radius:6px;overflow:hidden;margin:10px 0}.fresh-bar span{display:block;height:100%;background:#4b7ee8}.fresh-success{border:1px solid #cde9dc;background:#f3fbf7;color:#166548;border-radius:8px;padding:12px;font-size:14px;margin-bottom:10px}.fresh-danger-note{font-size:13px;color:#9d2f3a;margin-top:7px}.fresh-inline{display:flex;gap:8px}.fresh-inline input{flex:1}
@media(max-width:760px){.fresh-grid{grid-template-columns:1fr}.fresh-full{grid-column:auto}.fresh-modal .modalbox{width:98vw}.fresh-body{padding:14px}.fresh-actions{margin-left:-14px;margin-right:-14px;margin-bottom:-14px;padding:12px 14px}}
</style>'''


_FRESH_MODAL = r'''
<div id="freshModal" class="modal fresh-modal"><div class="modalbox">
  <div class="fresh-head"><div><h2>Cài WordPress mới</h2><p>Cài hoàn toàn mới qua FTP/FTPS, tạo wp-config, database và tài khoản quản trị.</p></div><button class="xbtn" onclick="closeFreshInstall()">×</button></div>
  <div class="fresh-body">
    <form id="freshForm" onsubmit="submitFreshInstall(event)">
      <div id="freshFormFields">
        <div class="fresh-section"><h3>1. Website & FTP</h3><div class="fresh-grid">
          <div class="field"><label>Tên dự án</label><input name="projectName" id="fi_project" placeholder="website-moi"></div>
          <div class="field"><label>Website URL *</label><input name="siteUrl" id="fi_url" required placeholder="https://example.com"></div>
          <div class="field"><label>FTP host *</label><input name="ftpHost" id="fi_host" required oninput="freshSyncDefaults()" placeholder="example.com"></div>
          <div class="field"><label>Tài khoản FTP *</label><input name="ftpUsername" required></div>
          <div class="field"><label>Mật khẩu FTP *</label><input name="ftpPassword" type="text" required></div>
          <div class="field"><label>Giao thức / Port</label><div class="fresh-inline"><select name="ftpProtocol"><option value="ftp">FTP</option><option value="ftps">FTPS</option></select><input name="ftpPort" type="number" value="21" style="max-width:110px"></div></div>
          <div class="field fresh-full"><label>Remote WordPress path *</label><input name="remotePath" id="fi_remote" required placeholder="/domains/example.com/public_html"></div>
        </div></div>

        <div class="fresh-section"><h3>2. Database</h3><p>FTP không tự tạo MySQL database. Chọn dùng DB có sẵn hoặc cấp MySQL account có quyền CREATE DATABASE/CREATE USER.</p><div class="fresh-grid">
          <div class="field"><label>Chế độ</label><select name="dbMode" id="fi_dbmode" onchange="freshToggleDbMode()"><option value="existing">Dùng database có sẵn</option><option value="create">Tạo database + user mới</option></select></div>
          <div class="field"><label>DB host</label><input name="dbHost" value="localhost"></div>
          <div class="field"><label>Database name *</label><input name="dbName" required placeholder="account_wp"></div>
          <div class="field"><label>Database user *</label><input name="dbUser" required placeholder="account_wp"></div>
          <div class="field fresh-full"><label>Database password *</label><input name="dbPassword" type="text" required></div>
        </div>
        <div id="fi_dbadmin" class="fresh-grid fresh-db-admin" style="margin-top:12px">
          <div class="field"><label>MySQL admin host</label><input name="mysqlAdminHost" value="localhost"></div>
          <div class="field"><label>MySQL admin user</label><input name="mysqlAdminUser"></div>
          <div class="field"><label>MySQL admin password</label><input name="mysqlAdminPassword" type="text"></div>
          <div class="field"><label>MySQL user host</label><input name="mysqlUserHost" value="localhost"><div class="hint">Thường là localhost trên shared hosting.</div></div>
        </div></div>

        <div class="fresh-section"><h3>3. WordPress</h3><div class="fresh-grid">
          <div class="field fresh-full"><label>Tên website *</label><input name="siteTitle" value="Website mới" required></div>
          <div class="field"><label>Admin username *</label><input name="adminUser" value="admin" required></div>
          <div class="field"><label>Admin email *</label><input name="adminEmail" type="email" required></div>
          <div class="field"><label>Admin password *</label><div class="fresh-inline"><input id="fi_adminpass" name="adminPassword" type="text" required><button class="btn btn-light" type="button" onclick="freshGeneratePassword()">Tạo</button></div></div>
          <div class="field"><label>Table prefix</label><input name="tablePrefix" value="wp_"></div>
        </div></div>

        <div class="fresh-section fresh-wipe"><h3>4. Thư mục đích</h3><label class="fresh-check"><input id="fi_wipe" name="wipeExisting" type="checkbox" onchange="freshToggleWipe()"><span><b>Xóa toàn bộ dữ liệu hiện có trong remote path trước khi cài.</b><br>Không bật mục này nếu thư mục hosting đang có website cần giữ.</span></label><div id="fi_confirmwrap" class="field fresh-confirm"><label>Nhập lại domain để xác nhận xóa</label><input id="fi_confirm" name="confirmText" placeholder="example.com"><div class="fresh-danger-note">Tool vẫn giữ .well-known nhưng các file/thư mục khác trong remote path sẽ bị xóa.</div></div></div>
      </div>

      <div id="freshProgress" class="fresh-section fresh-progress"><div class="fresh-progress-top"><div><h3 id="fi_status">Đang cài đặt</h3><p id="fi_message">Chuẩn bị...</p></div><span id="fi_percent" class="status status-blue">0%</span></div><div class="fresh-bar"><span id="fi_bar" style="width:2%"></span></div><pre id="fi_log" class="fresh-log"></pre><div id="fi_result"></div></div>
      <div class="fresh-actions"><button class="btn btn-light" type="button" onclick="closeFreshInstall()">Đóng</button><button id="fi_submit" class="btn btn-success" type="submit">Cài WordPress mới</button></div>
    </form>
  </div>
</div>
'''


_FRESH_JS = r'''
let freshPoll=null;
function openFreshInstall(){qs('#overlay').classList.add('show');qs('#freshModal').classList.add('show');qs('#freshFormFields').style.display='block';qs('#freshProgress').classList.remove('show');qs('#fi_submit').style.display='inline-flex';if(!qs('#fi_adminpass').value)freshGeneratePassword()}
function closeFreshInstall(){qs('#freshModal')?.classList.remove('show');if(freshPoll){clearInterval(freshPoll);freshPoll=null}if(!qs('#drawer').classList.contains('show')&&!qs('#createModal').classList.contains('show')&&!qs('#editModal').classList.contains('show'))qs('#overlay').classList.remove('show')}
function freshSyncDefaults(){const h=qs('#fi_host').value.trim();if(!qs('#fi_project').value)qs('#fi_project').value=h.replace(/[^a-zA-Z0-9._-]+/g,'-').toLowerCase();if(!qs('#fi_url').value)qs('#fi_url').value=h?`https://${h}`:'';if(!qs('#fi_remote').value)qs('#fi_remote').value=h?`/domains/${h}/public_html`:'';freshUpdateConfirmPlaceholder()}
function freshToggleDbMode(){qs('#fi_dbadmin').classList.toggle('show',qs('#fi_dbmode').value==='create')}
function freshToggleWipe(){qs('#fi_confirmwrap').classList.toggle('show',!!qs('#fi_wipe').checked);freshUpdateConfirmPlaceholder()}
function freshUpdateConfirmPlaceholder(){try{const host=new URL(qs('#fi_url').value).hostname||qs('#fi_host').value;qs('#fi_confirm').placeholder=host||'example.com'}catch{qs('#fi_confirm').placeholder=qs('#fi_host').value||'example.com'}}
function freshGeneratePassword(){const chars='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%';const a=new Uint32Array(22);crypto.getRandomValues(a);qs('#fi_adminpass').value=Array.from(a,x=>chars[x%chars.length]).join('')}
function freshPayload(){const fd=new FormData(qs('#freshForm')),d=Object.fromEntries(fd.entries());d.ftpPort=Number(d.ftpPort||21);d.workers=6;d.blockMb=1;d.passive=true;d.wipeExisting=qs('#fi_wipe').checked;return d}
async function submitFreshInstall(e){e.preventDefault();const d=freshPayload();if(d.wipeExisting){let host='';try{host=new URL(d.siteUrl).hostname}catch{}if(!host||d.confirmText!==host){toast('Hãy nhập chính xác domain '+host+' để xác nhận xóa.',true);return}}try{qs('#fi_submit').disabled=true;const j=await api('/api/fresh-install',{method:'POST',body:JSON.stringify(d)});qs('#freshFormFields').style.display='none';qs('#freshProgress').classList.add('show');qs('#fi_submit').style.display='none';renderFreshJob(j);freshPoll=setInterval(()=>pollFreshJob(j.id),700)}catch(err){qs('#fi_submit').disabled=false;toast(err.message,true)}}
function renderFreshJob(j){const logs=Array.isArray(j.logs)?j.logs:[];qs('#fi_status').textContent=j.status==='success'?'Cài đặt hoàn tất':j.status==='error'?'Cài đặt dừng do lỗi':'Đang cài đặt WordPress';qs('#fi_message').textContent=j.error||j.message||'';qs('#fi_percent').textContent=(j.percent||0)+'%';qs('#fi_percent').className='status '+(j.status==='success'?'status-green':j.status==='error'?'status-red':'status-blue');qs('#fi_bar').style.width=Math.max(2,j.percent||0)+'%';const log=qs('#fi_log');log.textContent=logs.join('\n')||'Đang chuẩn bị...';log.scrollTop=log.scrollHeight;if(j.status==='success'&&j.report){qs('#fi_result').innerHTML=`<div class="fresh-success">✓ WordPress ${esc(j.report.wordpress_version||'')} đã cài xong.<br>Website: <b>${esc(j.report.site_url||'')}</b><br>Files upload: ${esc(j.report.files_uploaded||0)}</div><div class="actions"><a class="btn btn-success" href="${esc(j.report.site_url||'')}" target="_blank">Mở website</a><a class="btn btn-light" href="${esc((j.report.site_url||'').replace(/\/$/,'')+'/wp-admin/')}" target="_blank">Mở wp-admin</a></div>`}else if(j.status==='error'){qs('#fi_result').innerHTML=`<div class="errorbox">${esc(j.error||'Cài đặt thất bại')}</div>`}else qs('#fi_result').innerHTML=''}
async function pollFreshJob(id){try{const j=await api('/api/fresh-install/jobs/'+encodeURIComponent(id));renderFreshJob(j);if(j.status!=='running'){clearInterval(freshPoll);freshPoll=null;qs('#fi_submit').disabled=false;if(j.status==='success'){await refreshAll();toast('Cài WordPress mới hoàn tất')}}}catch(e){clearInterval(freshPoll);freshPoll=null;toast(e.message,true)}}
const _closePanelsFreshBase=closePanels;closePanels=function(){closeFreshInstall();_closePanelsFreshBase()}
'''


def _render_app(token: str) -> str:
    html = _BASE_RENDER(token)
    html = html.replace(
        '<button class="btn btn-primary" onclick="openCreate()">Tạo dự án</button>',
        '<button class="btn btn-success" onclick="openFreshInstall()">Cài WordPress mới</button><button class="btn btn-primary" onclick="openCreate()">Tạo dự án</button>',
        1,
    )
    html = html.replace('<div id="toast" class="toast"></div>', _FRESH_MODAL + '\n<div id="toast" class="toast"></div>', 1)
    html = html.replace("</head>", _FRESH_CSS + "\n</head>", 1)
    return html.replace("</script>\n</body>", _FRESH_JS + "\n</script>\n</body>", 1)


def _fresh_progress(job: FreshGuiJob, req: FreshInstallRequest, event: dict[str, Any]) -> None:
    phase = str(event.get("phase") or "")
    message = str(event.get("message") or "")
    current = str(event.get("current") or "")
    completed = int(event.get("files_completed") or event.get("completed") or 0)
    total = int(event.get("files_total") or event.get("total") or 0)
    phase_base = {"ftp": 3, "wipe": 8, "database": 18, "download_core": 25, "download": 25, "upload": 35, "fresh_upload": 35, "config": 82, "install": 90, "done": 100}.get(phase, job.percent)
    if total and phase in {"fresh_upload", "upload"}:
        phase_base = 35 + int((completed / total) * 45)
    job.phase = phase
    job.percent = max(job.percent, min(100, phase_base))
    job.current = current
    if message:
        job.message = message
        job.log(message, req.secrets)
    elif current and (completed == 1 or completed == total or (completed and completed % 100 == 0)):
        job.log(f"{phase}: {completed}/{total} · {current}", req.secrets)


def _save_profile_after_install(req: FreshInstallRequest) -> None:
    name = server.wizard._slug(req.project_name or req.site_host)
    path = server.SITES_DIR / f"{name}.json"
    if path.exists():
        raise FileExistsError(f"Project {name} đã tồn tại; không ghi đè profile local.")
    server._json_write(path, {
        "host": req.ftp_host,
        "username": req.ftp_username,
        "password": req.ftp_password,
        "protocol": req.ftp_protocol,
        "port": req.ftp_port,
        "remotePath": req.remote_path,
        "siteUrl": req.site_url,
        "passive": req.passive,
        "workers": req.workers,
        "blockMb": req.block_mb,
    })


def _fresh_worker(job: FreshGuiJob, req: FreshInstallRequest) -> None:
    report_dir = server.REPORTS_DIR / req.site_host
    try:
        job.log("Bắt đầu cài WordPress mới", req.secrets)
        transport = server.wizard._transport(req.profile, req.ftp_password)
        report = run_fresh_install(req, transport=transport, report_dir=report_dir, progress=lambda e: _fresh_progress(job, req, e))
        _save_profile_after_install(req)
        job.report = report.public_dict()
        job.status = "success"
        job.percent = 100
        job.message = "WordPress mới đã được cài đặt thành công."
        job.log("Hoàn tất; đã tạo profile local để quản lý website về sau", req.secrets)
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "Cài đặt đã dừng. Không tự chạy lại thao tác xóa nếu chưa kiểm tra nguyên nhân."
        job.log(job.error, req.secrets)
        job.log(traceback.format_exc(limit=4), req.secrets)


def start_fresh_install(data: dict[str, Any]) -> FreshGuiJob:
    req = FreshInstallRequest.from_dict(data)
    project_slug = server.wizard._slug(req.project_name or req.site_host)
    if (server.SITES_DIR / f"{project_slug}.json").exists():
        raise FileExistsError(f"Dự án {project_slug} đã tồn tại. Fresh Install chỉ dành cho website mới.")
    job_id = f"{project_slug}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    job = FreshGuiJob(id=job_id, project=project_slug)
    with FRESH_LOCK:
        if any(item.status == "running" for item in FRESH_JOBS.values()):
            raise RuntimeError("Đang có một phiên cài WordPress mới chạy. Hãy chờ phiên đó hoàn tất.")
        FRESH_JOBS[job_id] = job
    threading.Thread(target=_fresh_worker, args=(job, req), daemon=True, name=f"wpclean-fresh-{project_slug}").start()
    return job


def _get_with_fresh(self) -> None:
    path = (urlparse(self.path).path.rstrip("/") or "/")
    match = re.fullmatch(r"/api/fresh-install/jobs/([^/]+)", path)
    if not match:
        _BASE_GET(self)
        return
    job = FRESH_JOBS.get(unquote(match.group(1)))
    if job is None:
        server._send_json(self, {"error": "Không tìm thấy phiên cài đặt."}, 404)
        return
    server._send_json(self, job.to_dict())


def _post_with_fresh(self) -> None:
    path = (urlparse(self.path).path.rstrip("/") or "/")
    if path != "/api/fresh-install":
        _BASE_POST(self)
        return
    if not self._authorized():
        server._send_json(self, {"error": "Phiên GUI không hợp lệ. Hãy refresh trang."}, 403)
        return
    try:
        body = server._read_body(self)
        server._send_json(self, start_fresh_install(body).to_dict(), 202)
    except Exception as exc:
        server._send_json(self, {"error": f"{type(exc).__name__}: {exc}"}, 400)


gui_ui.render_app = _render_app
server.GuiHandler.do_GET = _get_with_fresh
server.GuiHandler.do_POST = _post_with_fresh


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
