from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gui_server as server
from . import gui_ui
from .theme_restore import existing_child_theme_repair, plan_theme_stage


_ORIGINAL_PROJECT_PAYLOAD = server._project_payload
_ORIGINAL_CREATE_PROJECT = server.create_project
_ORIGINAL_RENDER_APP = gui_ui.render_app


_READABILITY_CSS = r'''<style id="wpclean-readability">
html,body{font-size:15px}
.brand strong{font-size:16px}.brand span{font-size:13px}.navbtn{font-size:14px}.side-note{font-size:13px}
.top-title{font-size:16px}.btn{font-size:14px}.page-head h1{font-size:28px}.page-head p{font-size:14px}
.summary-item span{font-size:12px}.summary-item b{font-size:21px}.panel-head h2{font-size:16px}.search{font-size:14px}
.project-header{font-size:12px}.project-name h3{font-size:15px}.project-name p{font-size:13px}.status{font-size:12px}
.current-step{font-size:13px}.progress-num{font-size:12px}.open-btn{font-size:13px}.empty{font-size:14px}
.drawer-title h2{font-size:22px}.drawer-title p{font-size:13px}.section-title{font-size:13px}
.kv span{font-size:12px}.kv b{font-size:13px}.step label{font-size:13px}.step small{font-size:12px}.stepdot{font-size:11px}
.action-card h3{font-size:15px}.action-card p{font-size:13px}.confirm-input{font-size:14px}
.check b{font-size:13px}.check span{font-size:12px}.job h3{font-size:14px}.job p{font-size:12px}.jobcur{font-size:12px}
.errorbox{font-size:12px}.logs div{font-size:11px}.foot-danger p{font-size:12px}
.modalhead h2{font-size:21px}.modalhead p{font-size:13px}.field label{font-size:12px}.field input,.field select{font-size:14px}
.hint{font-size:11px}.toast{font-size:13px}
</style>'''


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
    html = html.replace(
        "Đổi tài khoản, mật khẩu, port hoặc remote path. Mật khẩu để trống sẽ giữ nguyên mật khẩu đang lưu.",
        "Đổi tài khoản, mật khẩu, port hoặc remote path. Mật khẩu FTP được hiển thị trực tiếp vì giao diện chỉ chạy local trên máy này.",
    )
    html = html.replace('placeholder="Để trống nếu không đổi"', 'placeholder="Mật khẩu FTP"')
    return html.replace("</head>", _READABILITY_CSS + "\n</head>", 1)


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
server._project_payload = _project_payload
server.create_project = _create_or_update_project
server._theme_gate = _theme_gate
server.delete_project_local = _delete_project_local


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
