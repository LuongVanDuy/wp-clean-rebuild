from __future__ import annotations

import json
from pathlib import Path

from wpclean.mu_plugin_restore import run_mu_plugin_stage, scan_mu_plugins
from wpclean.site_config import SiteConnectionProfile


def _profile() -> SiteConnectionProfile:
    return SiteConnectionProfile(
        host="example.com",
        username="ftp-user",
        password="ftp-pass",
        protocol="ftp",
        port=21,
        remote_path="/public_html",
    )


def test_clean_mu_plugin_component_is_allowed(tmp_path: Path):
    root = tmp_path / "backup"
    mu = root / "mu-plugins"
    mu.mkdir(parents=True)
    (mu / "safe.php").write_text(
        "<?php\nadd_action('init', function () { do_action('example_safe_hook'); });\n",
        encoding="utf-8",
    )

    report = scan_mu_plugins(root)

    assert report.completed is True
    assert report.inventory_count == 1
    assert report.clean_components == 1
    assert report.blocked_components == 0
    assert report.components[0].blocked is False


def test_known_malware_marker_blocks_mu_plugin(tmp_path: Path, synthetic_code_samples):
    root = tmp_path / "backup"
    mu = root / "mu-plugins"
    mu.mkdir(parents=True)
    (mu / "bad.php").write_text(
        str(synthetic_code_samples["known_marker_php"]),
        encoding="utf-8",
    )

    report = scan_mu_plugins(root)

    component = report.components[0]
    assert component.blocked is True
    assert component.findings[0].score == 100
    assert component.findings[0].severity == "CRITICAL"
    assert report.blocked_components == 1


def test_one_bad_file_blocks_whole_mu_plugin_directory(tmp_path: Path, synthetic_code_samples):
    root = tmp_path / "backup"
    component = root / "mu-plugins" / "vendor-tool"
    component.mkdir(parents=True)
    (component / "loader.php").write_text("<?php add_action('init', 'vendor_init');", encoding="utf-8")
    (component / "payload.php").write_text(
        str(synthetic_code_samples["long_obfuscated_php"]),
        encoding="utf-8",
    )

    report = scan_mu_plugins(root)

    item = report.components[0]
    assert item.name == "vendor-tool"
    assert item.blocked is True
    assert item.files_total == 2
    assert any(finding.score >= 60 for finding in item.findings)
    assert report.clean_components == 0
    assert report.blocked_components == 1


def test_backup_exclusion_blocks_matching_mu_component(tmp_path: Path):
    root = tmp_path / "backup"
    mu = root / "mu-plugins"
    mu.mkdir(parents=True)
    (mu / "loader.php").write_text("<?php // safe but incomplete backup", encoding="utf-8")
    (root / "backup-report.json").write_text(
        json.dumps(
            {
                "exclusions": [
                    {
                        "stage": "mu-plugins",
                        "path": "/domains/example.com/public_html/wp-content/mu-plugins/loader.php",
                        "error": "FTP reset",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = scan_mu_plugins(root)

    assert report.components[0].blocked is True
    assert any("BACKUP EXCLUDED" in item for item in report.components[0].unreadable_files)


def test_run_stage_uploads_only_clean_components(tmp_path: Path, monkeypatch, synthetic_code_samples):
    root = tmp_path / "backup"
    mu = root / "mu-plugins"
    mu.mkdir(parents=True)
    (mu / "safe.php").write_text("<?php add_action('init', 'safe_init');", encoding="utf-8")
    (mu / "bad.php").write_text(
        str(synthetic_code_samples["known_vivid_php"]),
        encoding="utf-8",
    )

    uploaded: list[str] = []

    def fake_upload_file(_transport, remote_path: str, _local_path: Path):
        uploaded.append(remote_path)

    monkeypatch.setattr("wpclean.mu_plugin_restore._upload_file", fake_upload_file)
    report_path = tmp_path / "reports" / "rebuild-execute.json"

    report = run_mu_plugin_stage(
        profile=_profile(),
        transport=object(),
        backup_root=root,
        report_path=report_path,
    )

    assert uploaded == ["/public_html/wp-content/mu-plugins/safe.php"]
    assert report.completed is True
    assert report.files_uploaded == 1
    assert report.clean_components == 1
    assert report.blocked_components == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["mu_plugin_stage"]["blocked_components"] == 1


def test_upload_failure_keeps_mu_stage_incomplete_for_retry(tmp_path: Path, monkeypatch):
    root = tmp_path / "backup"
    mu = root / "mu-plugins"
    mu.mkdir(parents=True)
    (mu / "safe.php").write_text("<?php add_action('init', 'safe_init');", encoding="utf-8")

    def fail_upload(_transport, _remote_path: str, _local_path: Path):
        raise ConnectionResetError("FTP reset")

    monkeypatch.setattr("wpclean.mu_plugin_restore._upload_file", fail_upload)
    report_path = tmp_path / "reports" / "rebuild-execute.json"

    report = run_mu_plugin_stage(
        profile=_profile(),
        transport=object(),
        backup_root=root,
        report_path=report_path,
    )

    assert report.completed is False
    assert report.files_uploaded == 0
    assert report.blocked_components == 1
    assert any("UPLOAD FAILED" in item for item in report.components[0].unreadable_files)
