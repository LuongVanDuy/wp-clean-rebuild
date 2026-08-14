from __future__ import annotations

import sys
from typing import Any

from rich.prompt import Prompt

from . import operator_wizard as wizard
from . import plugin_workflow as plugin_module
from . import rebuild_entry as theme_module
from .mu_plugin_restore import run_mu_plugin_stage
from .operator_locale import VietnameseConsoleProxy


_original_infer_status = wizard._infer_status
_original_show_status = wizard._show_status
_original_stage_plugin = wizard._stage_plugin


def _infer_status(paths) -> dict[str, Any]:
    status = _original_infer_status(paths)
    execution = wizard._read_json(paths["execute"])
    mu_stage = execution.get("mu_plugin_stage") if isinstance(execution.get("mu_plugin_stage"), dict) else {}
    status["mu_plugin"] = mu_stage
    status["mu_plugin_done"] = bool(mu_stage.get("completed"))
    status["mu_plugin_blocked"] = int(mu_stage.get("blocked_components") or 0)
    status["mu_plugin_uploaded"] = int(mu_stage.get("files_uploaded") or 0)
    return status


def _show_status(profile, paths, status: dict[str, Any]) -> None:
    _original_show_status(profile, paths, status)
    if status.get("mu_plugin_done"):
        detail = f"đã xử lý | upload {status.get('mu_plugin_uploaded', 0)} file"
        if status.get("mu_plugin_blocked"):
            detail += f" | chặn {status['mu_plugin_blocked']} component nghi vấn"
        wizard.console.print(f"[green]✓[/green] MU-plugin: {detail}")
    elif status.get("rebuild_ready"):
        wizard.console.print("[dim]·[/dim] MU-plugin: chưa quét/khôi phục")


def _next_stage(status: dict[str, Any]) -> str:
    """Resume from the furthest trusted stage; never move backward after rebuild."""
    final_ok = status["final_status"] in {"PASS", "PASS WITH WARNINGS"}

    # A final PASS created before MU-plugin support is not sufficient. Existing
    # projects must run the newly introduced MU-plugin gate once, then final
    # verification is regenerated after any clean MU-plugin upload.
    if final_ok and (
        not status.get("rebuild_ready")
        or (
            status.get("theme_done")
            and status.get("plugin_done")
            and status.get("mu_plugin_done", False)
            and (not status.get("plugin_manual") or status.get("manual_plugins_ack"))
        )
    ):
        return "done"

    # Once core/database rebuild is proven complete, backup/clean/preflight are
    # historical prerequisites. Never send an operator backward into them.
    if status["rebuild_ready"]:
        if not status["theme_done"]:
            return "theme"
        # The existing run loop already owns the "plugin" stage. Our patched
        # plugin handler runs normal plugins first, then MU-plugins immediately
        # afterwards. Returning "plugin" also safely resumes an unfinished
        # MU-plugin stage without repeating earlier destructive work.
        if not status["plugin_done"] or not status.get("mu_plugin_done", False):
            return "plugin"
        if status["plugin_manual"] and not status["manual_plugins_ack"]:
            return "manual-plugins"
        return "final"

    if not status["filesystem_backup"]:
        return "backup-files"
    if not status["database_backup"]:
        return "backup-db"
    if not status["scan_ready"]:
        return "verify-scan"
    if not status["clean_ready"]:
        return "clean"
    if not status["preflight_ready"]:
        return "preflight"
    return "rebuild"


def _stage_plugin(profile, transport, paths) -> None:
    status = _infer_status(paths)
    if not status["plugin_done"]:
        _original_stage_plugin(profile, transport, paths)
        status = _infer_status(paths)

    if not status["plugin_done"]:
        raise wizard.TamDungQuyTrinh(
            "Plugin thường chưa hoàn tất. Chạy BATDAU lại để tiếp tục trước khi xử lý MU-plugin."
        )

    if status.get("mu_plugin_done"):
        wizard.console.print("[green]✓ MU-plugin đã được quét/khôi phục ở lần chạy trước.[/green]")
        return

    wizard.console.print("\n[bold cyan]BƯỚC 11B — MU-PLUGIN[/bold cyan]")
    wizard.console.print(
        "Backup MU-plugin chỉ dùng làm nguồn kiểm tra. Component nào sạch mới được upload; "
        "component có HIGH/CRITICAL hoặc file không đọc được sẽ bị chặn toàn bộ."
    )

    with wizard.console.status("[cyan]Đang quét và khôi phục MU-plugin an toàn...[/cyan]", spinner="dots") as status_ui:
        def progress(event: dict) -> None:
            if event.get("phase") != "upload_mu_plugin":
                return
            current = str(event.get("current", ""))
            if len(current) > 65:
                current = "…" + current[-64:]
            status_ui.update(f"[cyan]Đang upload MU-plugin sạch: {current}[/cyan]")

        result = run_mu_plugin_stage(
            profile=profile,
            transport=transport,
            backup_root=paths["backup"],
            report_path=paths["execute"],
            progress=progress,
        )

    if result.inventory_count == 0:
        wizard.console.print("[green]✓ Backup không có MU-plugin cần khôi phục.[/green]")
    else:
        wizard.console.print(
            f"Đã kiểm tra {result.inventory_count} component | sạch={result.clean_components} | "
            f"bị chặn={result.blocked_components} | file đã upload={result.files_uploaded}"
        )

    blocked = [item for item in result.components if item.blocked]
    if blocked:
        wizard.console.print("\n[bold yellow]MU-plugin bị chặn, KHÔNG upload:[/bold yellow]")
        for component in blocked:
            wizard.console.print(f" - [yellow]{component.name}[/yellow]")
            for finding in component.findings:
                if finding.score < 60:
                    continue
                relative = finding.path
                try:
                    relative = str(wizard.Path(finding.path).relative_to(wizard.Path(result.source_path)))
                except Exception:
                    pass
                wizard.console.print(
                    f"   [red]{finding.severity} {finding.score}/100[/red] {relative}: "
                    + "; ".join(finding.reasons)
                )
            for unreadable in component.unreadable_files[:10]:
                wizard.console.print(f"   [red]Không an toàn/không đọc được:[/red] {unreadable}")
        wizard.console.print(
            "[cyan]Các component trên vẫn nằm nguyên trong backup gốc nhưng không được đưa trở lại website.[/cyan]"
        )

    if not result.completed:
        raise wizard.TamDungQuyTrinh(
            "MU-plugin stage chưa hoàn tất do lỗi đọc/upload. Chạy BATDAU lại hoặc báo kỹ thuật kiểm tra report mu-plugin-stage.json."
        )

    # Any MU-plugin upload changes the live runtime after an earlier final check.
    # Force final verification to run again even when the project had PASS before
    # this stage was introduced.
    paths["final"].unlink(missing_ok=True)
    wizard.console.print("[green]✓ MU-plugin stage hoàn tất; chỉ component vượt qua scan mới được upload.[/green]")
    wizard.console.print("[cyan]Kết quả kiểm tra cuối cũ (nếu có) đã được bỏ để website được verify lại sau MU-plugin.[/cyan]")


def _stage_manual_plugins(profile, paths, status: dict[str, Any]) -> None:
    count = int(status.get("plugin_manual") or 0)
    wizard.console.print("\n[bold yellow]BƯỚC 12 — PLUGIN CẦN CÀI THỦ CÔNG[/bold yellow]")
    wizard.console.print(f"Có [bold]{count}[/bold] plugin không có trên WordPress.org.")
    wizard.console.print(
        "Vui lòng lấy bản sạch từ nhà cung cấp rồi upload thủ công. "
        "Không copy plugin code cũ từ backup."
    )
    wizard.console.print("1 = Tôi đã upload xong")
    wizard.console.print("2 = Tôi chấp nhận tiếp tục và xử lý sau")
    wizard.console.print("3 = Tạm dừng")
    choice = Prompt.ask("Lựa chọn", choices=["1", "2", "3"], default="1")
    if choice == "3":
        raise wizard.TamDungQuyTrinh(
            "Đã tạm dừng để xử lý plugin thủ công. Chạy BATDAU lại sau khi hoàn tất."
        )
    wizard._save_operator_state(
        paths,
        profile,
        manual_plugins_ack=True,
        manual_plugins_note="uploaded" if choice == "1" else "accepted-for-later",
    )


# Apply Vietnamese operator-facing output to the two engine modules that still
# contain a few English status labels. Technical terms such as SHA-256/FTP and
# raw exception details are intentionally preserved.
_operator_console = VietnameseConsoleProxy(wizard.console)
theme_module.console = _operator_console
plugin_module.console = _operator_console

# Patch the original wizard module in one place so its run loop uses the hardened
# routing, adds MU-plugin handling immediately after plugins, and keeps the
# existing tested workflow engine intact.
wizard._infer_status = _infer_status
wizard._show_status = _show_status
wizard._next_stage = _next_stage
wizard._stage_plugin = _stage_plugin
wizard._stage_manual_plugins = _stage_manual_plugins
wizard.STAGE_LABELS["plugin"] = "Cài plugin sạch + quét/khôi phục MU-plugin"


run = wizard.run


if __name__ == "__main__":
    sys.exit(run())
