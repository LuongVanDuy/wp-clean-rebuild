from __future__ import annotations

import json
from pathlib import Path

import pytest

from wpclean import project_delete_command as project_delete


def _configure_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(project_delete, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(project_delete, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(project_delete, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(project_delete, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(project_delete, "REPAIRS_DIR", tmp_path / "repairs")


def _profile(path: Path, host: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "host": host,
                "username": "ftp-user",
                "password": "ftp-pass",
                "protocol": "ftp",
                "port": 21,
                "remotePath": f"/domains/{host}/public_html",
                "siteUrl": f"https://{host}",
            }
        ),
        encoding="utf-8",
    )


def test_only_final_pass_projects_are_deletable(tmp_path: Path, monkeypatch):
    _configure_roots(tmp_path, monkeypatch)
    _profile(project_delete.SITES_DIR / "done.json", "done.example")
    _profile(project_delete.SITES_DIR / "blocked.json", "blocked.example")

    done_report = project_delete.REPORTS_DIR / "done.example" / "final-verify.json"
    done_report.parent.mkdir(parents=True, exist_ok=True)
    done_report.write_text('{"status":"PASS"}', encoding="utf-8")

    blocked_report = project_delete.REPORTS_DIR / "blocked.example" / "final-verify.json"
    blocked_report.parent.mkdir(parents=True, exist_ok=True)
    blocked_report.write_text('{"status":"BLOCKED"}', encoding="utf-8")

    projects = project_delete._completed_projects()

    assert [item["name"] for item in projects] == ["done"]
    assert projects[0]["host"] == "done.example"


def test_pass_with_warnings_is_considered_completed(tmp_path: Path, monkeypatch):
    _configure_roots(tmp_path, monkeypatch)
    _profile(project_delete.SITES_DIR / "warning.json", "warning.example")
    report = project_delete.REPORTS_DIR / "warning.example" / "final-verify.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"status":"PASS WITH WARNINGS"}', encoding="utf-8")

    projects = project_delete._completed_projects()

    assert len(projects) == 1
    assert projects[0]["status"] == "PASS WITH WARNINGS"


def test_delete_local_path_removes_only_inside_allowed_root(tmp_path: Path):
    allowed = tmp_path / "backups"
    target = allowed / "example.com"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("{}", encoding="utf-8")

    assert project_delete._delete_local_path(target, allowed) is True
    assert not target.exists()

    outside = tmp_path / "do-not-delete"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="Từ chối xóa"):
        project_delete._delete_local_path(outside, allowed)
    assert outside.exists()
