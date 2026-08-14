from __future__ import annotations

import sys
from typing import Any

from rich.prompt import Prompt

from . import operator_wizard as wizard
from . import plugin_workflow as plugin_module
from . import rebuild_entry as theme_module
from .operator_locale import VietnameseConsoleProxy


def _next_stage(status: dict[str, Any]) -> str:
    """Resume from the furthest trusted stage; never move backward after rebuild."""
    if status["final_status"] in {"PASS", "PASS WITH WARNINGS"}:
        return "done"

    # Once core/database rebuild is proven complete, backup/clean/preflight are
    # historical prerequisites. Never send an operator backward into them.
    if status["rebuild_ready"]:
        if not status["theme_done"]:
            return "theme"
        if not status["plugin_done"]:
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
# routing and the cleaner Vietnamese manual-plugin prompt.
wizard._next_stage = _next_stage
wizard._stage_manual_plugins = _stage_manual_plugins


run = wizard.run


if __name__ == "__main__":
    sys.exit(run())
