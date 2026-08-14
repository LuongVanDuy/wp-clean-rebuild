from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wpclean import gui_server
from wpclean.gui_ui import render_app
from wpclean.rebuild_entry import app


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


def test_gui_html_is_self_contained_and_vietnamese() -> None:
    html = render_app("test-token")

    assert "WP Clean Rebuild" in html
    assert "Quản lý xử lý WordPress" in html
    assert "Tạo dự án mới" in html
    assert "Xác nhận rebuild WordPress" in html
    assert "test-token" in html
    assert "https://cdn" not in html
    assert "unpkg.com" not in html


def test_gui_create_project_writes_local_profile(tmp_path: Path, monkeypatch) -> None:
    _sandbox_gui(tmp_path, monkeypatch)

    project = gui_server.create_project(
        {
            "name": "khach-hang-a",
            "host": "example.test",
            "username": "ftp-user",
            "password": "ftp-pass",
            "protocol": "ftp",
            "port": 21,
            "remotePath": "/domains/example.test/public_html",
            "siteUrl": "https://example.test",
            "workers": 4,
            "blockMb": 1,
            "passive": True,
        }
    )

    profile = tmp_path / "sites" / "khach-hang-a.json"
    assert profile.is_file()
    assert project["name"] == "khach-hang-a"
    assert project["host"] == "example.test"
    assert project["nextStage"] == "backup-files"
    assert project["completed"] is False


def test_gui_rejects_duplicate_project(tmp_path: Path, monkeypatch) -> None:
    _sandbox_gui(tmp_path, monkeypatch)
    payload = {
        "name": "same",
        "host": "example.test",
        "username": "u",
        "password": "p",
    }
    gui_server.create_project(payload)

    try:
        gui_server.create_project(payload)
    except FileExistsError:
        pass
    else:
        raise AssertionError("duplicate GUI project should be rejected")


def test_gui_does_not_remove_existing_recovery_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "rebuild-resume-db-config" in result.stdout
    assert "rebuild-theme-config" in result.stdout
    assert "rebuild-plugin-config" in result.stdout
    assert "verify-live-config" in result.stdout
