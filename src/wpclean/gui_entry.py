from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
import sys

from . import gui_server as server
from . import gui_ui
from .theme_restore import existing_child_theme_repair, plan_theme_stage


_ORIGINAL_PROJECT_PAYLOAD = server._project_payload
_ORIGINAL_CREATE_PROJECT = server.create_project
_ORIGINAL_RENDER_APP = gui_ui.render_app
_ORIGINAL_RUN_PIPELINE = server._run_pipeline
_ORIGINAL_PROGRESS = server._progress
_ANSI_RE = server.ANSI_RE


_READABILITY_CSS = r'''<style id="wpclean-readability">
html,body{font-size:16px;line-height:1.45}
.layout{display:block!important}.sidebar{display:none!important}.main{width:100%;min-width:0}.content{max-width:1800px;padding-left:32px;padding-right:32px}
.brand strong{font-size:18px}.brand span{font-size:14px}.navbtn{font-size:16px}.side-note{font-size:14px}
.top-title{font-size:18px}.btn{font-size:15px}.page-head h1{font-size:32px}.page-head p{font-size:16px}
.summary-item span{font-size:13px}.summary-item b{font-size:24px}.panel-head h2{font-size:18px}.search{font-size:15px}
.project-header{font-size:13px}.project-name h3{font-size:17px}.project-name p{font-size:15px}.status{font-size:13px}
.current-step{font-size:15px}.progress-num{font-size:13px}.open-btn{font-size:15px}.empty{font-size:16px}
.drawer{width:min(1380px,98vw)}.drawer-title h2{font-size:25px}.drawer-title p{font-size:15px}.drawer-body{padding:22px 24px 36px}.section-title{font-size:14px}
.detail-layout{display:grid;grid-template-columns:minmax(470px,.9fr) minmax(560px,1.1fr);gap:24px;align-items:start}.detail-left,.detail-right{min-width:0}.detail-right{position:sticky;top:88px}
.kv span{font-size:13px}.kv b{font-size:15px}.step label{font-size:15px}.step small{font-size:13px}.stepdot{font-size:12px}
.action-card h3{font-size:17px}.action-card p{font-size:15px}.confirm-input{font-size:15px}
.check b{font-size:15px}.check span{font-size:13px}.job h3{font-size:16px}.job p{font-size:14px}.jobcur{font-size:13px}
.errorbox{font-size:13px}.job .logs{display:none}.foot-danger p{font-size:13px}
.system-health{display:inline-flex;align-items:center;gap:7px;margin-right:6px;color:#64748b;font-size:13px}.system-health-dot{width:8px;height:8px;border-radius:50%;background:#16a34a;box-shadow:0 0 0 3px #dcfce7}.system-health.offline{color:#b42332}.system-health.offline .system-health-dot{background:#dc2626;box-shadow:0 0 0 3px #fee2e2}
.btn:focus-visible,.xbtn:focus-visible,.open-btn:focus-visible,.navbtn:focus-visible,.detail-tab:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid rgba(37,99,235,.28);outline-offset:2px}.btn[aria-busy="true"]{cursor:wait;opacity:.72}
.job-health{display:flex;align-items:center;gap:7px;font-size:13px;font-weight:600;color:#475569}.job-health-dot{width:9px;height:9px;border-radius:50%;background:#64748b}.job-health.active .job-health-dot{background:#16a34a;box-shadow:0 0 0 4px #dcfce7;animation:wpclean-pulse 1.8s ease-out infinite}.job-health.waiting .job-health-dot,.job-health.slow .job-health-dot{background:#d97706;box-shadow:0 0 0 4px #fef3c7}.job-health.stalled .job-health-dot,.job-health.error .job-health-dot{background:#dc2626;box-shadow:0 0 0 4px #fee2e2}.job-health.success .job-health-dot{background:#15805d}
.jobmeta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:11px 0}.jobmetric{border:1px solid var(--line);border-radius:7px;background:#fff;padding:8px 9px}.jobmetric span{display:block;color:#7b8797;font-size:11px}.jobmetric b{display:block;margin-top:2px;color:#334155;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums}.jobbar.indeterminate span{width:34%!important;animation:wpclean-slide 1.3s ease-in-out infinite}
.error-summary{margin-top:12px;border:1px solid #efc9cf;background:#fff7f8;border-radius:9px;padding:13px;color:#852733}.error-summary h4{margin:0 0 5px;font-size:15px}.error-code{display:inline-flex;margin-bottom:7px;border-radius:5px;background:#fee2e2;color:#991b1b;padding:4px 7px;font:600 12px/1.2 Consolas,monospace}.error-summary p{margin:0 0 8px;font-size:13px;line-height:1.55}.error-recovery{border-top:1px solid #f3d4d8;padding-top:8px;color:#6d3037}.technical-details{margin-top:9px}.technical-details summary{cursor:pointer;font-size:12px;font-weight:600;color:#64748b}.technical-details pre{max-height:190px;overflow:auto;margin:8px 0 0;padding:9px;border-radius:6px;background:#111827;color:#cbd5e1;white-space:pre-wrap;word-break:break-word;font:11px/1.5 Consolas,monospace}
.terminal-panel{border:1px solid #273244;border-radius:10px;background:#111827;overflow:hidden;box-shadow:0 8px 24px rgba(15,23,42,.08)}
.terminal-head{min-height:56px;padding:8px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #2a3548;background:#172033;color:#e5e7eb}.terminal-title{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:600;letter-spacing:.2px}.terminal-dot{width:8px;height:8px;border-radius:50%;background:#39c985}.terminal-meta{font-size:12px;color:#9ca9bb;font-weight:400}.terminal-copy{border:1px solid #3a465a;background:#202a3c;color:#dbe4f0;border-radius:6px;padding:7px 10px;min-height:36px;font-size:12px;font-weight:500;cursor:pointer}.terminal-copy:hover{background:#29364a}.terminal-output{height:calc(100vh - 186px);min-height:560px;margin:0;padding:15px 16px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#0d1422;color:#cbd5e1;font:14px/1.58 Consolas,"Cascadia Mono","Courier New",monospace;scrollbar-color:#3b4a61 #111827}.terminal-line{display:block}.terminal-line-error{color:#fda4af}.terminal-line-pass{color:#86efac}.terminal-empty{color:#718096}
.modalhead h2{font-size:24px}.modalhead p{font-size:15px}.field label{font-size:14px}.field input,.field select{font-size:16px}
.hint{font-size:12px}.toast{font-size:14px}
@media(max-width:1120px){.drawer{width:min(920px,98vw)}.detail-layout{grid-template-columns:1fr}.detail-right{position:relative;top:auto}.terminal-output{height:440px;min-height:360px}}
@media(max-width:820px){.content{padding-left:14px;padding-right:14px}.jobmeta{grid-template-columns:repeat(2,minmax(0,1fr))}.system-health{display:none}}
@keyframes wpclean-pulse{0%{box-shadow:0 0 0 0 rgba(22,163,74,.35)}70%{box-shadow:0 0 0 7px rgba(22,163,74,0)}100%{box-shadow:0 0 0 0 rgba(22,163,74,0)}}@keyframes wpclean-slide{0%{transform:translateX(-110%)}50%{transform:translateX(95%)}100%{transform:translateX(300%)}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
</style>'''


_DETAIL_JS = r'''
state.logFollow=state.logFollow!==false;
function logLineHtml(line){const text=String(line||''),cls=/ERROR|\[[A-Z][A-Z0-9-]+-\d{3}\]/.test(text)?' terminal-line-error':/PASS|Hoàn tất|thành công/i.test(text)?' terminal-line-pass':'';return `<span class="terminal-line${cls}">${esc(text)}</span>`}
function terminalPanelHtml(p){
  const j=p.job||{};
  const logs=Array.isArray(j.logs)?j.logs:[];
  const status=j.healthLabel||(j.status==='running'?'Đang chạy':j.status==='error'?'Có lỗi':j.status==='needs-action'?'Chờ xác nhận':j.status==='paused'?'Tạm dừng':j.status==='success'?'Hoàn tất':'Sẵn sàng');
  const body=logs.length?logs.map(logLineHtml).join(''):'Chưa có log trong phiên GUI này. Nhấn Tiếp tục để bắt đầu xử lý.';
  const signal=j.updatedAt?` · ${formatSignal(j.idleSeconds)}`:'';
  return `<div class="terminal-panel"><div class="terminal-head"><div><div class="terminal-title"><span class="terminal-dot"></span>PROJECT LOG</div><div class="terminal-meta">${esc(status)}${signal} · ${logs.length} dòng</div></div><div class="terminal-tools"><button id="terminalFollow" class="terminal-copy" type="button" onclick="toggleTerminalFollow()">Tự cuộn: ${state.logFollow?'Bật':'Tắt'}</button><button class="terminal-copy" type="button" onclick="copyTerminalLog()">Sao chép</button></div></div><pre id="terminalOutput" class="terminal-output ${logs.length?'':'terminal-empty'}" role="log" aria-live="polite" aria-relevant="additions text">${body}</pre></div>`;
}
function scrollTerminalBottom(){const out=qs('#terminalOutput');if(out)out.scrollTop=out.scrollHeight}
function toggleTerminalFollow(){state.logFollow=!state.logFollow;const button=qs('#terminalFollow');if(button)button.textContent=`Tự cuộn: ${state.logFollow?'Bật':'Tắt'}`;if(state.logFollow)scrollTerminalBottom()}
async function copyTerminalLog(){
  const el=qs('#terminalOutput');
  if(!el)return;
  try{await navigator.clipboard.writeText(el.textContent||'');toast('Đã sao chép log')}catch(e){toast('Không thể sao chép log tự động',true)}
}
renderDrawer=function(p){
  state.selected=p.name;
  const pc=progressOf(p);
  qs('#drawerContent').innerHTML=`<div class="drawer-head"><div class="drawer-title"><div><h2>${esc(p.host)}</h2><p>${esc(p.name)} · ${pc}% hoàn tất</p></div><button class="xbtn" type="button" aria-label="Đóng chi tiết dự án" onclick="closePanels()">×</button></div></div><div class="drawer-body"><div class="detail-layout"><div class="detail-left"><div class="section"><div class="section-head"><div class="section-title">Kết nối FTP</div></div>${connectionHtml(p)}</div><div class="section"><div class="section-head"><div class="section-title">Tiến độ xử lý</div></div><div class="steps">${stepHtml(p)}</div>${jobHtml(p)}${decisionHtml(p)}</div>${p.themeRepair?`<div class="section"><button class="btn btn-warning" onclick="openRepair('${esc(p.name)}')">Mở thư mục theme repair</button></div>`:''}${p.completed?`<div class="foot-danger"><button class="btn btn-danger" onclick="deleteProject('${esc(p.name)}')">Xóa dự án local</button></div>`:''}</div><div class="detail-right">${terminalPanelHtml(p)}</div></div></div>`;
  if(state.logFollow)setTimeout(scrollTerminalBottom,0);
}
'''


def _render_app(token: str) -> str:
    html = _ORIGINAL_RENDER_APP(token)
    # The GUI binds only to 127.0.0.1. The operator explicitly wants the saved
    # FTP password visible as plain text for easier local project management.
    html = html.replace('type="password"', 'type="text"')
    html = html.replace(
        "${c.passwordConfigured?'Đã lưu':'Chưa có'}",
        "${esc(c.password||'Chưa có')}",
    )
    html = html.replace("qs('#e_password').value='';", "qs('#e_password').value=c.password||'';")
    html = html.replace('placeholder="Để trống nếu không đổi"', 'placeholder="Mật khẩu FTP"')
    html = html.replace("</head>", _READABILITY_CSS + "\n</head>", 1)
    return html.replace("</script>\n</body>", _DETAIL_JS + "\n</script>\n</body>", 1)


class _GuiTerminalStream:
    def __init__(self, job, mirror, prefix: str = "") -> None:
        self.job = job
        self.mirror = mirror
        self.prefix = prefix
        self.buffer = ""
        self.encoding = getattr(mirror, "encoding", "utf-8") or "utf-8"

    def write(self, text: str) -> int:
        if not text:
            return 0
        try:
            self.mirror.write(text)
        except Exception:
            pass
        clean = _ANSI_RE.sub("", str(text)).replace("\r", "\n")
        self.buffer += clean
        parts = self.buffer.split("\n")
        self.buffer = parts.pop()
        for line in parts:
            line = line.strip()
            if line:
                self.job.log(f"{self.prefix}{line}")
        if len(self.buffer) > 1600:
            line = self.buffer.strip()
            self.buffer = ""
            if line:
                self.job.log(f"{self.prefix}{line}")
        return len(text)

    def flush(self) -> None:
        if self.buffer.strip():
            self.job.log(f"{self.prefix}{self.buffer.strip()}")
            self.buffer = ""
        try:
            self.mirror.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False


def _progress_with_terminal_log(job, event: dict[str, Any]) -> None:
    _ORIGINAL_PROGRESS(job, event)
    phase = str(event.get("phase") or "")
    if not phase:
        return
    bucket = (int(job.percent) // 10) * 10
    attempt = int(event.get("attempt") or 0)
    key = f"{phase}:{bucket}:{attempt}"
    if getattr(job, "_gui_progress_log_key", "") == key:
        return
    setattr(job, "_gui_progress_log_key", key)
    current = str(
        event.get("current")
        or event.get("current_file")
        or event.get("current_dir")
        or event.get("remote_path")
        or event.get("file")
        or event.get("stage")
        or ""
    ).strip()
    suffix = f" · {bucket}%" if bucket else ""
    if job.total_items:
        suffix += f" · {job.completed_items}/{job.total_items} {job.progress_unit or 'file'}"
    if attempt:
        suffix += f" · retry {attempt}/{event.get('max_attempts') or '?'}"
    if current:
        suffix += f" · {current}"
    job.log(f"{job.message or phase}{suffix}")


def _run_pipeline_with_terminal(name: str, options: dict[str, Any], job) -> None:
    stdout_mirror = sys.__stdout__ or sys.stdout
    stderr_mirror = sys.__stderr__ or sys.stderr
    stdout_stream = _GuiTerminalStream(job, stdout_mirror)
    stderr_stream = _GuiTerminalStream(job, stderr_mirror, prefix="ERROR | ")
    job.log("Bắt đầu phiên xử lý GUI")
    try:
        with redirect_stdout(stdout_stream), redirect_stderr(stderr_stream):
            _ORIGINAL_RUN_PIPELINE(name, options, job)
    finally:
        stdout_stream.flush()
        stderr_stream.flush()


def _project_payload(name: str) -> dict[str, Any]:
    payload = _ORIGINAL_PROJECT_PAYLOAD(name)
    _profile_path, profile, _paths = server._profile_and_paths(name)
    payload["connection"] = {
        "host": profile.host,
        "username": profile.username,
        "password": profile.password or "",
        "protocol": profile.protocol,
        "port": profile.port,
        "remotePath": profile.remote_path,
        "siteUrl": profile.web_base_url,
        "passive": profile.passive,
        "workers": profile.workers,
        "blockMb": profile.block_mb,
        "passwordConfigured": bool(profile.password),
    }
    return payload


def _project_has_started(profile, paths: dict[str, Path]) -> bool:
    evidence = [
        paths["backup"],
        paths["preflight"],
        paths["execute"],
        paths["scan"],
        paths["final"],
        server.REPORTS_DIR / profile.host / "operator-state.json",
    ]
    return any(path.exists() for path in evidence)


def _update_project(name: str, data: dict[str, Any]) -> dict[str, Any]:
    profile_path, profile, paths = server._profile_and_paths(name)
    with server.JOBS_LOCK:
        job = server.JOBS.get(name)
        if server.ACTIVE_PROJECT == name or (job and job.status == "running"):
            raise RuntimeError("Dự án đang chạy. Hãy chờ bước hiện tại dừng trước khi sửa FTP.")

    raw = server._json_read(profile_path)
    initial_password_fingerprint = str(raw.get("initialFtpPasswordFingerprint") or "")
    if not initial_password_fingerprint:
        initial_password_fingerprint = server._secret_fingerprint(str(raw.get("password") or profile.password or ""))
    new_host = str(data.get("host") or profile.host).strip()
    username = str(data.get("username") or profile.username).strip()
    password = str(data.get("password") or raw.get("password") or "")
    protocol = str(data.get("protocol") or profile.protocol).strip().lower()
    if not new_host or not username or not password:
        raise ValueError("FTP host, tài khoản và mật khẩu không được để trống.")
    if protocol not in {"ftp", "ftps", "ftp+tls", "ftp-tls"}:
        raise ValueError("Giao thức chỉ hỗ trợ FTP hoặc FTPS.")
    if new_host != profile.host and _project_has_started(profile, paths):
        raise ValueError(
            "Không thể đổi FTP host sau khi project đã bắt đầu. Có thể sửa tài khoản, mật khẩu, port và remote path; "
            "nếu đây là website khác hãy tạo project mới."
        )

    try:
        port = int(data.get("port") or profile.port)
        workers = max(1, min(16, int(data.get("workers") or profile.workers)))
        block_mb = max(1, min(8, int(data.get("blockMb") or profile.block_mb)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Port, workers hoặc block MiB không hợp lệ.") from exc

    remote_path = str(data.get("remotePath") or profile.remote_path).strip()
    site_url = str(data.get("siteUrl") or profile.web_base_url).strip()
    if not remote_path:
        raise ValueError("Remote WordPress path không được để trống.")

    raw.update(
        {
            "host": new_host,
            "username": username,
            "password": password,
            "protocol": protocol,
            "port": port,
            "remotePath": remote_path,
            "siteUrl": site_url,
            "passive": bool(data.get("passive", raw.get("passive", True))),
            "workers": workers,
            "blockMb": block_mb,
            "initialFtpPasswordFingerprint": initial_password_fingerprint,
        }
    )
    server._json_write(profile_path, raw)
    server.JOBS.pop(name, None)
    return _project_payload(name)


def _create_or_update_project(data: dict[str, Any]) -> dict[str, Any]:
    update_name = str(data.get("_updateProject") or "").strip()
    if update_name:
        return _update_project(update_name, data)
    return _ORIGINAL_CREATE_PROJECT(data)


def _theme_gate(profile, paths, job) -> bool:
    active, _child_root = plan_theme_stage(paths["backup"])

    # For unsupported/detection-unavailable themes, let the existing engine write
    # theme_stage into rebuild-execute.json first. That keeps CLI and GUI resume
    # semantics identical; manual_theme_ack can then advance the project safely.
    if active is None or not active.is_flatsome:
        result = server.rebuild_entry._run_theme_stage(
            profile=profile,
            transport=server._transport(profile),
            backup_root=paths["backup"],
            report_path=paths["execute"],
        )
        theme_name = result.unsupported_theme or (active.stylesheet if active else "Không xác định")
        server._gate(
            job,
            "theme",
            "Theme cần cài thủ công",
            f"Website dùng theme {theme_name}. Hãy cài bản sạch rồi xác nhận để tiếp tục.",
            {"type": "manual-theme", "theme": theme_name},
        )
        return True

    repair = existing_child_theme_repair(paths["backup"], active.stylesheet) if active.has_child else None
    server._gate(
        job,
        "theme",
        "Cài theme an toàn",
        "Flatsome sẽ lấy từ package tin cậy. Theme con chỉ upload sau khi scan PASS.",
        {
            "type": "theme",
            "template": active.template,
            "stylesheet": active.stylesheet,
            "hasChild": active.has_child,
            "childSlug": active.stylesheet if active.has_child else "",
            "repairPath": str(repair or ""),
        },
    )
    return True


def _delete_project_local(name: str, confirmation: str) -> None:
    from . import project_delete_command as delete_module

    if confirmation != name:
        raise ValueError("Tên xác nhận không khớp.")
    payload = server._project_payload(name)
    if not payload.get("completed"):
        raise RuntimeError("Chỉ được xóa project local sau khi dự án đã hoàn tất.")

    profile_path, profile, paths = server._profile_and_paths(name)
    backup_path = Path(paths["backup"])
    # A resumed/new run can use backups/<host>-<timestamp>; delete exactly the
    # backup root remembered by operator-state.json, never a guessed path.
    delete_module._delete_local_path(backup_path, server.BACKUPS_DIR)
    delete_module._delete_local_path(server.REPORTS_DIR / profile.host, server.REPORTS_DIR)
    delete_module._delete_local_path(server.REPAIRS_DIR / profile.host, server.REPAIRS_DIR)
    delete_module._delete_local_path(profile_path, server.SITES_DIR)
    server.JOBS.pop(name, None)


gui_ui.render_app = _render_app
server._progress = _progress_with_terminal_log
server._run_pipeline = _run_pipeline_with_terminal
server._project_payload = _project_payload
server.create_project = _create_or_update_project
server._theme_gate = _theme_gate
server.delete_project_local = _delete_project_local


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
