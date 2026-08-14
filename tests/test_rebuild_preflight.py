from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from wpclean.backup import write_manifest
from wpclean.rebuild_preflight import run_rebuild_preflight
from wpclean.transport.ftp import RemoteFile


class FakeTransport:
    def __init__(self, files: list[RemoteFile], *, tls: bool = True):
        self._files = files
        self.config = SimpleNamespace(tls=tls)

    def list_files_recursive(self, remote_root: str):
        return list(self._files)


def _make_verified_backup(tmp_path: Path) -> Path:
    backup = tmp_path / "backup"
    (backup / "database").mkdir(parents=True)
    (backup / "uploads").mkdir(parents=True)
    (backup / "config").mkdir(parents=True)
    (backup / "database" / "original.sql").write_text("-- sql\n", encoding="utf-8")
    (backup / "uploads" / "image.jpg").write_bytes(b"image")
    (backup / "config" / "wp-config.php").write_text("<?php\n", encoding="utf-8")
    (backup / "backup-report.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    write_manifest(backup)

    clean = backup / "clean"
    (clean / "database").mkdir(parents=True)
    (clean / "uploads").mkdir(parents=True)
    (clean / "database" / "clean.sql").write_text("-- clean sql\n", encoding="utf-8")
    (clean / "uploads" / "image.jpg").write_bytes(b"image")
    write_manifest(clean)
    return backup


def test_preflight_passes_known_core_backed_uploads_and_well_known(tmp_path: Path):
    backup = _make_verified_backup(tmp_path)
    remote_root = "/domains/example.com/public_html"
    files = [
        RemoteFile(f"{remote_root}/index.php", 12),
        RemoteFile(f"{remote_root}/wp-admin/admin.php", 100),
        RemoteFile(f"{remote_root}/wp-includes/load.php", 100),
        RemoteFile(f"{remote_root}/wp-content/uploads/image.jpg", 5),
        RemoteFile(f"{remote_root}/.well-known/acme-challenge/token", 10),
    ]
    report = run_rebuild_preflight(
        host="example.com",
        transport=FakeTransport(files),
        remote_root=remote_root,
        backup_root=backup,
        report_path=tmp_path / "report.json",
    )
    assert report.ready_for_destructive_rebuild is True
    assert report.blocked_files == 0
    assert report.preserve_files == 1
    assert report.wipe_files == 4


def test_preflight_blocks_unknown_unbacked_file(tmp_path: Path):
    backup = _make_verified_backup(tmp_path)
    remote_root = "/domains/example.com/public_html"
    files = [RemoteFile(f"{remote_root}/mystery-shell.php.bak", 123)]
    report = run_rebuild_preflight(
        host="example.com",
        transport=FakeTransport(files),
        remote_root=remote_root,
        backup_root=backup,
        report_path=tmp_path / "report.json",
    )
    assert report.ready_for_destructive_rebuild is False
    assert report.blocked_files == 1
    assert report.blocked[0].path == "mystery-shell.php.bak"


def test_preflight_blocks_backed_file_size_drift(tmp_path: Path):
    backup = _make_verified_backup(tmp_path)
    remote_root = "/domains/example.com/public_html"
    files = [RemoteFile(f"{remote_root}/wp-content/uploads/image.jpg", 999)]
    report = run_rebuild_preflight(
        host="example.com",
        transport=FakeTransport(files),
        remote_root=remote_root,
        backup_root=backup,
        report_path=tmp_path / "report.json",
    )
    assert report.ready_for_destructive_rebuild is False
    assert report.blocked_files == 1
    assert "drifted after backup" in report.blocked[0].reason


def test_preflight_warns_on_plain_ftp(tmp_path: Path):
    backup = _make_verified_backup(tmp_path)
    remote_root = "/domains/example.com/public_html"
    report = run_rebuild_preflight(
        host="example.com",
        transport=FakeTransport([], tls=False),
        remote_root=remote_root,
        backup_root=backup,
        report_path=tmp_path / "report.json",
    )
    assert report.ready_for_destructive_rebuild is True
    assert any("plain FTP" in warning for warning in report.warnings)
