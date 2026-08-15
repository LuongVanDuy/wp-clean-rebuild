from __future__ import annotations

import json
from pathlib import Path

from wpclean import gui_journal_entry
from wpclean import gui_server
from wpclean.gui_ui import render_app
from wpclean.project_journal import append_activity, read_activity, reconcile_automatic_todos, set_todo_status


def _sandbox_gui(tmp_path: Path, monkeypatch) -> None:
    sites = tmp_path / "sites"
    backups = tmp_path / "backups"
    reports = tmp_path / "reports"
    repairs = tmp_path / "repairs"
    for path in (sites, backups, reports, repairs):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gui_server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gui_server, "SITES_DIR", sites)
    monkeypatch.setattr(gui_server, "BACKUPS_DIR", backups)
    monkeypatch.setattr(gui_server, "REPORTS_DIR", reports)
    monkeypatch.setattr(gui_server, "REPAIRS_DIR", repairs)
    monkeypatch.setattr(gui_server.wizard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gui_server.wizard, "SITES_DIR", sites)
    monkeypatch.setattr(gui_server.wizard, "BACKUPS_DIR", backups)
    monkeypatch.setattr(gui_server.wizard, "REPORTS_DIR", reports)
    gui_server.JOBS.clear()
    gui_server.ACTIVE_PROJECT = None


def test_activity_log_persists_and_redacts_password(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "example.test"
    append_activity(
        report_dir,
        project="demo",
        stage="ftp",
        message="Login ftp-user secret-pass completed",
        secrets=["secret-pass"],
    )
    append_activity(report_dir, project="demo", stage="backup", message="Backup PASS")

    stored = (report_dir / "activity-log.jsonl").read_text(encoding="utf-8")
    assert "secret-pass" not in stored
    assert "***" in stored
    rows = read_activity(report_dir)
    assert len(rows) == 2
    assert rows[-1]["message"] == "Backup PASS"


def test_reconcile_creates_named_plugin_and_theme_todos(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "example.test"
    execution = {
        "theme_stage": {
            "unsupported_theme": "woodmart",
            "mode": "manual",
            "child_theme_slug": "duyanhweb",
            "child_repair_workspace": "repairs/example/themes/duyanhweb/working-copy",
            "child_installed": False,
        },
        "plugin_stage": {
            "classifications": [
                {
                    "status": "manual",
                    "inventory": {"slug": "advanced-custom-fields-pro", "name": "Advanced Custom Fields PRO"},
                    "detail": "Not found",
                },
                {
                    "status": "lookup-error",
                    "inventory": {"slug": "network-plugin", "name": "Network Plugin"},
                    "detail": "WordPress.org timeout",
                },
            ]
        },
    }
    todos = reconcile_automatic_todos(report_dir, execution=execution, operator_state={})
    titles = [item["title"] for item in todos]

    assert "Cài theme woodmart từ nguồn sạch" in titles
    assert "Cài plugin Advanced Custom Fields PRO" in titles
    assert "Kiểm tra lại nguồn plugin Network Plugin" in titles
    assert "Sửa và quét lại theme con duyanhweb" in titles
    assert all(item["status"] == "pending" for item in todos)

    plugin = next(item for item in todos if item["kind"] == "plugin")
    set_todo_status(report_dir, plugin["id"], completed=True)
    reloaded = reconcile_automatic_todos(report_dir, execution=execution, operator_state={})
    assert next(item for item in reloaded if item["id"] == plugin["id"])["status"] == "done"


def test_workflow_ack_resolves_manual_items(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "example.test"
    execution = {
        "theme_stage": {"unsupported_theme": "woodmart"},
        "plugin_stage": {
            "classifications": [
                {
                    "status": "manual",
                    "inventory": {"slug": "acf-pro", "name": "ACF Pro"},
                    "detail": "Not found",
                }
            ]
        },
    }
    todos = reconcile_automatic_todos(
        report_dir,
        execution=execution,
        operator_state={"manual_theme_ack": True, "manual_plugins_ack": True, "manual_plugins_note": "uploaded"},
    )
    assert todos
    assert all(item["status"] == "done" for item in todos)


def test_gui_payload_reads_history_and_todos_after_restart(tmp_path: Path, monkeypatch) -> None:
    _sandbox_gui(tmp_path, monkeypatch)
    project = gui_server.create_project(
        {
            "name": "history-demo",
            "host": "example.test",
            "username": "ftp-user",
            "password": "ftp-pass",
            "remotePath": "/public_html",
            "siteUrl": "https://example.test",
        }
    )
    report_dir = tmp_path / "reports" / "example.test"
    append_activity(report_dir, project="history-demo", stage="backup", message="Hôm qua backup PASS")
    (report_dir / "rebuild-execute.json").write_text(
        json.dumps(
            {
                "plugin_stage": {
                    "classifications": [
                        {
                            "status": "manual",
                            "inventory": {"slug": "acf-pro", "name": "ACF Pro"},
                            "detail": "Not found",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    payload = gui_server._project_payload(project["name"])
    assert payload["activity"][-1]["message"] == "Hôm qua backup PASS"
    assert payload["pendingTodoCount"] == 1
    assert payload["todos"][0]["title"] == "Cài plugin ACF Pro"
    assert payload["connection"]["password"] == "ftp-pass"


def test_journal_gui_has_three_project_tabs() -> None:
    html = render_app("journal-token")
    assert "Tiến độ" in html
    assert "Lịch sử" in html
    assert "Việc cần làm" in html
    assert "activity-log.jsonl" in html
    assert "Đánh dấu hoàn tất" in html
    assert "wpclean-journal-style" in html


def test_launcher_keeps_journal_through_hidden_fresh_gui_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "giaodien.ps1").read_text(encoding="utf-8-sig")
    assert "python -m wpclean.gui_no_fresh_entry" in script

    hidden_entry = (root / "src" / "wpclean" / "gui_no_fresh_entry.py").read_text(encoding="utf-8")
    assert "gui_fresh_safe_entry" in hidden_entry

    safety_entry = (root / "src" / "wpclean" / "gui_fresh_safe_entry.py").read_text(encoding="utf-8")
    assert "gui_fresh_entry" in safety_entry

    fresh_entry = (root / "src" / "wpclean" / "gui_fresh_entry.py").read_text(encoding="utf-8")
    assert "gui_journal_entry" in fresh_entry
