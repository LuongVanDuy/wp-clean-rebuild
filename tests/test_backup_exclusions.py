import json
from pathlib import Path

from wpclean.backup import verify_backup_completeness, verify_manifest, write_manifest
from wpclean.remote_backup import _item_from_stats
from wpclean.transport.ftp import TransferFailure, TransferStats


def test_partial_directory_becomes_verified_exclusion_not_blocker(tmp_path: Path):
    backup = tmp_path / "backup"
    (backup / "database").mkdir(parents=True)
    (backup / "uploads").mkdir(parents=True)
    (backup / "themes").mkdir(parents=True)
    (backup / "config").mkdir(parents=True)

    (backup / "database" / "original.sql").write_text("-- sql\n", encoding="utf-8")
    (backup / "uploads" / "image.jpg").write_bytes(b"image")
    (backup / "config" / "wp-config.php").write_text("<?php\n", encoding="utf-8")

    report = {
        "items": [
            {
                "remote_path": "/public_html/wp-content/uploads",
                "status": "ok",
            },
            {
                "remote_path": "/public_html/wp-content/themes",
                "status": "ok-with-exclusions",
                "files_failed": 1,
                "failed_files": [
                    {
                        "path": "/public_html/wp-content/themes/rmqvsww/about.php",
                        "error": "FTP download failed after retry budget",
                    }
                ],
            },
            {
                "remote_path": "/public_html/wp-config.php",
                "status": "ok",
            },
        ]
    }
    (backup / "backup-report.json").write_text(json.dumps(report), encoding="utf-8")

    completeness = verify_backup_completeness(backup, require_database=True)
    assert completeness.complete is True
    assert completeness.problems == []

    manifest = write_manifest(backup)
    ok, problems = verify_manifest(backup, manifest)
    assert ok is True
    assert problems == []


def test_whole_required_stage_failure_still_blocks(tmp_path: Path):
    backup = tmp_path / "backup"
    (backup / "database").mkdir(parents=True)
    (backup / "uploads").mkdir(parents=True)
    (backup / "config").mkdir(parents=True)
    (backup / "database" / "original.sql").write_text("-- sql\n", encoding="utf-8")
    (backup / "config" / "wp-config.php").write_text("<?php\n", encoding="utf-8")
    (backup / "backup-report.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "remote_path": "/public_html/wp-content/themes",
                        "status": "transfer-failed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completeness = verify_backup_completeness(backup, require_database=True)
    assert completeness.complete is False
    assert any("wp-content/themes" in problem for problem in completeness.problems)


def test_transfer_stats_marks_some_failed_files_as_ok_with_exclusions(tmp_path: Path):
    stats = TransferStats(
        files_total=10,
        files_downloaded=8,
        files_skipped=1,
        files_failed=1,
        bytes_downloaded=100,
        elapsed_seconds=1.0,
        failures=[TransferFailure(path="/public_html/wp-content/themes/rmqvsww/about.php", error="reset")],
    )

    item = _item_from_stats(
        "/public_html/wp-content/themes",
        tmp_path / "themes",
        "directory",
        stats,
    )

    assert item.status == "ok-with-exclusions"
    assert item.files_failed == 1
    assert "EXCLUDED 1 unreadable file" in (item.error or "")


def test_transfer_stats_blocks_when_every_file_failed(tmp_path: Path):
    stats = TransferStats(
        files_total=1,
        files_downloaded=0,
        files_skipped=0,
        files_failed=1,
        bytes_downloaded=0,
        elapsed_seconds=1.0,
        failures=[TransferFailure(path="/public_html/wp-content/themes/bad.php", error="reset")],
    )

    item = _item_from_stats(
        "/public_html/wp-content/themes",
        tmp_path / "themes",
        "directory",
        stats,
    )

    assert item.status == "transfer-failed"
