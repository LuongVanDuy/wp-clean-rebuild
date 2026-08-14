from __future__ import annotations

from pathlib import Path

from . import gui_server as server
from .theme_restore import existing_child_theme_repair, plan_theme_stage


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


server._theme_gate = _theme_gate
server.delete_project_local = _delete_project_local


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
