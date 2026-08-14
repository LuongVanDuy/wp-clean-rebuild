from __future__ import annotations

from io import StringIO
import json
import re
from pathlib import Path

from typer.testing import CliRunner

from wpclean import gui_entry  # activates GUI runtime patches
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
    gui_server.ACTIVE_PROJECT = None


def test_gui_html_is_self_contained_vietnamese_and_readable() -> None:
    html = render_app("test-token")

    assert "WP Clean Rebuild" in html
    assert "Dự án WordPress" in html
    assert "Tạo dự án mới" in html
    assert "Xác nhận rebuild WordPress" in html
    assert "Sửa kết nối FTP" in html
    assert "Lưu & kiểm tra kết nối" in html
    assert "wpclean-readability" in html
    assert "html,body{font-size:16px" in html
    assert "page-head h1{font-size:32px}" in html
    assert "project-name h3{font-size:17px}" in html
    assert "field input,.field select{font-size:16px}" in html
    assert "drawer{width:min(1380px,98vw)}" in html
    assert "detail-layout{display:grid" in html
    assert "LOG XỬ LÝ" in html
    assert "terminalOutput" in html
    assert 'type="password"' not in html
    assert "test-token" in html
    assert "https://cdn" not in html
    assert "unpkg.com" not in html
    assert "linear-gradient" not in html
    assert "backdrop-filter" not in html

    weights = [int(item) for item in re.findall(r"font-weight:(\d+)", html)]
    assert weights
    assert max(weights) <= 700


def test_gui_terminal_stream_captures_stdout_and_keeps_history() -> None:
    job = gui_server.GuiJob(project="terminal-test")
    mirror = StringIO()
    stream = gui_entry._GuiTerminalStream(job, mirror)

    stream.write("Files discovered: 120\n")
    stream.write("\x1b[31mwarning line\x1b[0m\n")
    stream.flush()

    assert "Files discovered: 120" in mirror.getvalue()
    assert any("Files discovered: 120" in line for line in job.logs)
    assert any("warning line" in line for line in job.logs)
    assert all("\x1b" not in line for line in job.logs)

    for index in range(350):
        job.log(f"line-{index}")
    assert len(job.logs) >= 350
    payload = job.to_dict()
    assert len(payload["logs"]) == 300
    assert "line-349" in payload["logs"][-1]


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
    assert project["connection"]["username"] == "ftp-user"
    assert project["connection"]["passwordConfigured"] is True
    assert project["connection"]["password"] == "ftp-pass"


def test_gui_can_update_ftp_and_keep_password_when_blank(tmp_path: Path, monkeypatch) -> None:
    _sandbox_gui(tmp_path, monkeypatch)
    gui_server.create_project(
        {
            "name": "edit-me",
            "host": "example.test",
            "username": "old-user",
            "password": "old-pass",
            "protocol": "ftp",
            "port": 21,
            "remotePath": "/public_html",
            "siteUrl": "https://example.test",
        }
    )

    updated = gui_server.create_project(
        {
            "_updateProject": "edit-me",
            "host": "example.test",
            "username": "new-user",
            "password": "",
            "protocol": "ftps",
            "port": 2121,
            "remotePath": "/domains/example.test/public_html",
            "siteUrl": "https://example.test",
            "workers": 6,
            "blockMb": 2,
            "passive": True,
        }
    )

    raw = json.loads((tmp_path / "sites" / "edit-me.json").read_text(encoding="utf-8"))
    assert raw["username"] == "new-user"
    assert raw["password"] == "old-pass"
    assert raw["protocol"] == "ftps"
    assert raw["port"] == 2121
    assert updated["connection"]["username"] == "new-user"
    assert updated["connection"]["port"] == 2121
    assert updated["connection"]["password"] == "old-pass"


def test_gui_can_replace_wrong_ftp_password(tmp_path: Path, monkeypatch) -> None:
    _sandbox_gui(tmp_path, monkeypatch)
    gui_server.create_project(
        {
            "name": "wrong-login",
            "host": "example.test",
            "username": "ftp-user",
            "password": "wrong-pass",
        }
    )

    updated = gui_server.create_project(
        {
            "_updateProject": "wrong-login",
            "username": "ftp-user",
            "password": "correct-pass",
        }
    )

    raw = json.loads((tmp_path / "sites" / "wrong-login.json").read_text(encoding="utf-8"))
    assert raw["password"] == "correct-pass"
    assert updated["connection"]["password"] == "correct-pass"


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
