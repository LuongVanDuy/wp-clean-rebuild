from __future__ import annotations

from pathlib import Path

import pytest

from wpclean import gui_ftp_logging_entry as ftp_gui
from wpclean import gui_server
from wpclean.project_journal import read_activity


def _sandbox(tmp_path: Path, monkeypatch) -> str:
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
    with ftp_gui._FTP_TEST_LOCK:
        ftp_gui._FTP_TEST_ACTIVE.clear()

    project = gui_server.create_project(
        {
            "name": "ftp-log-demo",
            "host": "ftp.example.test",
            "username": "ftp-user",
            "password": "ftp-secret",
            "protocol": "ftp",
            "port": 21,
            "remotePath": "/public_html",
            "siteUrl": "https://example.test",
        }
    )
    return project["name"]


def test_manual_ftp_test_is_visible_in_terminal_and_persistent_history(tmp_path: Path, monkeypatch) -> None:
    name = _sandbox(tmp_path, monkeypatch)

    monkeypatch.setattr(
        ftp_gui,
        "_BASE_TEST_CONNECTION",
        lambda _name: {"ok": True, "cwd": "/", "remotePath": "/public_html", "tls": False},
    )

    result = ftp_gui._test_connection_with_log(name)
    assert result["ok"] is True

    job = gui_server.JOBS[name]
    joined = "\n".join(job.logs)
    assert "FTP TEST · bắt đầu kết nối ftp.example.test:21" in joined
    assert "FTP TEST · PASS" in joined

    activity = read_activity(tmp_path / "reports" / "ftp.example.test")
    messages = "\n".join(str(item.get("message") or "") for item in activity)
    assert "FTP TEST · bắt đầu kết nối ftp.example.test:21" in messages
    assert "FTP TEST · PASS" in messages
    assert "ftp-secret" not in messages


def test_manual_ftp_timeout_is_logged_with_friendly_message(tmp_path: Path, monkeypatch) -> None:
    name = _sandbox(tmp_path, monkeypatch)

    def timeout(_name):
        raise TimeoutError("[WinError 10060] A connection attempt failed")

    monkeypatch.setattr(ftp_gui, "_BASE_TEST_CONNECTION", timeout)

    with pytest.raises(RuntimeError, match="FTP TEST · TIMEOUT"):
        ftp_gui._test_connection_with_log(name)

    job = gui_server.JOBS[name]
    assert any("FTP TEST · TIMEOUT" in line for line in job.logs)
    activity = read_activity(tmp_path / "reports" / "ftp.example.test")
    assert any("FTP TEST · TIMEOUT" in str(item.get("message") or "") for item in activity)
    assert any(item.get("level") == "error" for item in activity)


def test_duplicate_ftp_test_is_rejected_without_duplicate_log(tmp_path: Path, monkeypatch) -> None:
    name = _sandbox(tmp_path, monkeypatch)
    job = ftp_gui._diagnostic_job(name, gui_server._profile_and_paths(name)[1])

    assert ftp_gui._claim_ftp_test(name) is True
    try:
        with pytest.raises(RuntimeError, match="FTP TEST đang chạy"):
            ftp_gui._test_connection_with_log(name)
    finally:
        ftp_gui._release_ftp_test(name)

    assert not any("bắt đầu kết nối" in line for line in job.logs)
    activity = read_activity(tmp_path / "reports" / "ftp.example.test")
    assert not any("bắt đầu kết nối" in str(item.get("message") or "") for item in activity)


def test_ftp_error_messages_classify_common_failures() -> None:
    assert "TIMEOUT" in ftp_gui._ftp_error_message(TimeoutError("WinError 10060"), host="ftp.test", port=21)
    assert "LOGIN FAILED" in ftp_gui._ftp_error_message(RuntimeError("530 Login authentication failed"), host="ftp.test", port=21)
    assert "CONNECTION REFUSED" in ftp_gui._ftp_error_message(ConnectionRefusedError("WinError 10061"), host="ftp.test", port=21)


def test_production_gui_injects_immediate_ftp_test_terminal_feedback() -> None:
    html = ftp_gui._render_with_ftp_test_log("test-token")
    assert "ftpTestClientLine" in html
    assert "ftpTestsInFlight" in html
    assert "FTP TEST · đang kết nối" in html
    assert "FTP đang được kiểm tra. Vui lòng chờ kết quả hiện tại." in html
    assert "/test',{method:'POST'" in html
    assert "Cài WordPress mới</button>" not in html


def test_launcher_uses_parallel_production_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "giaodien.ps1").read_text(encoding="utf-8-sig")
    assert "Chay-GuiEntry -Module 'wpclean.gui_parallel_entry'" in script
    assert "Chay-GuiEntry -Module 'wpclean.gui_ftp_logging_entry'" in script
    assert "gui-startup.log" in script
    assert "Nhấn Enter để đóng cửa sổ" in script
