from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gui_server as server
from .theme_restore import existing_child_theme_repair, plan_theme_stage


_ORIGINAL_PROJECT_PAYLOAD = server._project_payload
_ORIGINAL_CREATE_PROJECT = server.create_project


def _project_payload(name: str) -> dict[str, Any]:
    payload = _ORIGINAL_PROJECT_PAYLOAD(name)
    _profile_path, profile, _paths = server._profile_and_paths(name)
    payload["connection"] = {
        "host": profile.host,
        "username": profile.username,
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


server._project_payload = _project_payload
server.create_project = _create_or_update_project
server._theme_gate = _theme_gate
server.delete_project_local = _delete_project_local


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
