from __future__ import annotations

import json
from pathlib import Path

import pytest

from wpclean.rebuild_resume import _bridge_error_detail, resume_database_import
from wpclean.site_config import SiteConnectionProfile


def test_bridge_error_detail_preserves_mysql_statement_and_errno():
    body = json.dumps(
        {
            "ok": False,
            "message": "SQL import failed",
            "statement": 123,
            "errno": 1064,
            "error": "syntax error near example",
        }
    ).encode("utf-8")

    detail = _bridge_error_detail(body, status=500)

    assert "HTTP 500" in detail
    assert "SQL import failed" in detail
    assert "statement=123" in detail
    assert "mysql_errno=1064" in detail
    assert "syntax error near example" in detail


def test_bridge_error_detail_keeps_non_json_server_body_short():
    detail = _bridge_error_detail(b"<html><body>Server failure</body></html>", status=500)
    assert detail.startswith("HTTP 500:")
    assert "Server failure" in detail


def test_resume_refuses_when_report_does_not_prove_destructive_stage_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backup_root = tmp_path / "backup"
    clean_root = backup_root / "clean"
    clean_sql = clean_root / "database" / "clean.sql"
    clean_sql.parent.mkdir(parents=True)
    clean_sql.write_text("SELECT 1;\n", encoding="utf-8")
    (clean_root / "manifest.json").write_text("{}", encoding="utf-8")

    report_path = tmp_path / "rebuild-execute.json"
    report_path.write_text(
        json.dumps(
            {
                "host": "example.com",
                "remote_root": "/public_html",
                "wiped_files": 0,
                "core_uploaded": 0,
                "wp_config_uploaded": False,
                "htaccess_uploaded": False,
                "database_imported": False,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("wpclean.rebuild_resume.verify_manifest", lambda root, manifest: (True, []))

    profile = SiteConnectionProfile(
        host="example.com",
        username="user",
        password="pass",
        protocol="ftp",
        port=21,
        remote_path="/public_html",
    )

    class NoNetworkTransport:
        pass

    with pytest.raises(ValueError, match="does not prove the rebuild reached database-import stage"):
        resume_database_import(
            profile=profile,
            transport=NoNetworkTransport(),  # type: ignore[arg-type]
            backup_root=backup_root,
            execution_report_path=report_path,
        )
