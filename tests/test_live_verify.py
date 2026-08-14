from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wpclean.live_verify import HttpCheck, verify_live_site
from wpclean.rebuild_entry import app
from wpclean.site_config import SiteConnectionProfile
from wpclean.transport import RemoteFile


class FakeTransport:
    class Config:
        workers = 2

    config = Config()

    def __init__(self, files: list[RemoteFile]):
        self.files = files

    def list_files_recursive(self, remote_root: str):
        return self.files


PROFILE = SiteConnectionProfile(
    host="example.com",
    username="user",
    password="pass",
    protocol="ftp",
    port=21,
    remote_path="/public_html",
    site_url="https://example.com",
)


def _execution_report(tmp_path: Path) -> Path:
    path = tmp_path / "rebuild-execute.json"
    path.write_text('{"wordpress_version":"7.0.4"}', encoding="utf-8")
    return path


def test_verify_live_command_is_registered():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "verify-live-config" in result.stdout


def test_clean_live_site_passes(tmp_path: Path, monkeypatch):
    files = [
        RemoteFile("/public_html/index.php", 10),
        RemoteFile("/public_html/wp-admin/admin.php", 10),
        RemoteFile("/public_html/wp-includes/version.php", 10),
        RemoteFile("/public_html/wp-content/themes/duyanhweb/functions.php", 20),
    ]
    transport = FakeTransport(files)

    monkeypatch.setattr(
        "wpclean.live_verify._fetch_core_checksums",
        lambda version: {
            "index.php": "a" * 32,
            "wp-admin/admin.php": "b" * 32,
            "wp-includes/version.php": "c" * 32,
        },
    )
    monkeypatch.setattr(
        "wpclean.live_verify._hash_remote_files",
        lambda *args, **kwargs: (
            {
                "index.php": "a" * 32,
                "wp-admin/admin.php": "b" * 32,
                "wp-includes/version.php": "c" * 32,
            },
            [],
        ),
    )
    monkeypatch.setattr(
        "wpclean.live_verify._download_scan_file",
        lambda transport, remote_root, item, temp_root: (
            "wp-content/themes/duyanhweb/functions.php",
            b"<?php add_action('init', function () {});",
        ),
    )
    monkeypatch.setattr(
        "wpclean.live_verify._http_check",
        lambda url, admin=False, timeout=25: HttpCheck(
            url=url,
            ok=True,
            status=200,
            final_url="https://example.com/wp-login.php" if admin else "https://example.com/",
        ),
    )

    report = verify_live_site(
        profile=PROFILE,
        transport=transport,  # type: ignore[arg-type]
        report_path=_execution_report(tmp_path),
    )

    assert report.status == "PASS"
    assert report.core_verified == 3
    assert report.issues == []


def test_core_checksum_mismatch_blocks(tmp_path: Path, monkeypatch):
    files = [RemoteFile("/public_html/index.php", 10)]
    transport = FakeTransport(files)
    monkeypatch.setattr("wpclean.live_verify._fetch_core_checksums", lambda version: {"index.php": "a" * 32})
    monkeypatch.setattr(
        "wpclean.live_verify._hash_remote_files",
        lambda *args, **kwargs: ({"index.php": "f" * 32}, []),
    )
    monkeypatch.setattr("wpclean.live_verify._download_scan_file", lambda *args, **kwargs: ("", b""))
    monkeypatch.setattr(
        "wpclean.live_verify._http_check",
        lambda url, admin=False, timeout=25: HttpCheck(url=url, ok=True, status=200, final_url=url),
    )

    report = verify_live_site(
        profile=PROFILE,
        transport=transport,  # type: ignore[arg-type]
        report_path=_execution_report(tmp_path),
    )

    assert report.status == "BLOCKED"
    assert report.core_mismatched == 1
    assert any(issue.category == "core-checksum" for issue in report.issues)


def test_php_under_uploads_blocks(tmp_path: Path, monkeypatch):
    files = [RemoteFile("/public_html/wp-content/uploads/2026/08/shell.php", 20)]
    transport = FakeTransport(files)
    monkeypatch.setattr("wpclean.live_verify._fetch_core_checksums", lambda version: {})
    monkeypatch.setattr(
        "wpclean.live_verify._download_scan_file",
        lambda transport, remote_root, item, temp_root: (
            "wp-content/uploads/2026/08/shell.php",
            b"<?php echo 'x';",
        ),
    )
    monkeypatch.setattr(
        "wpclean.live_verify._http_check",
        lambda url, admin=False, timeout=25: HttpCheck(url=url, ok=True, status=200, final_url=url),
    )

    report = verify_live_site(
        profile=PROFILE,
        transport=transport,  # type: ignore[arg-type]
        report_path=_execution_report(tmp_path),
    )

    assert report.status == "BLOCKED"
    assert report.uploads_executables == 1
    assert any(issue.category == "uploads-executable" for issue in report.issues)


def test_known_vivid_marker_blocks(tmp_path: Path, monkeypatch):
    files = [RemoteFile("/public_html/wp-content/themes/duyanhweb/functions.php", 50)]
    transport = FakeTransport(files)
    monkeypatch.setattr("wpclean.live_verify._fetch_core_checksums", lambda version: {})
    monkeypatch.setattr(
        "wpclean.live_verify._download_scan_file",
        lambda transport, remote_root, item, temp_root: (
            "wp-content/themes/duyanhweb/functions.php",
            b"<?php $path = '/wp-content/mu-plugins/vivid-toolkit-tap.php';",
        ),
    )
    monkeypatch.setattr(
        "wpclean.live_verify._http_check",
        lambda url, admin=False, timeout=25: HttpCheck(url=url, ok=True, status=200, final_url=url),
    )

    report = verify_live_site(
        profile=PROFILE,
        transport=transport,  # type: ignore[arg-type]
        report_path=_execution_report(tmp_path),
    )

    assert report.status == "BLOCKED"
    assert report.suspicious_markers == 1
    assert any(issue.category == "known-malware-marker" for issue in report.issues)
