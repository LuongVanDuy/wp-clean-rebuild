from __future__ import annotations

from dataclasses import asdict, dataclass, field
from ftplib import error_perm
from pathlib import Path, PurePosixPath
import json

from .backup import verify_manifest, write_manifest
from .transport import FTPTransport, TransferStats


@dataclass(slots=True)
class BackupItemReport:
    remote_path: str
    local_path: str
    kind: str
    status: str
    files_total: int = 0
    files_downloaded: int = 0
    files_skipped: int = 0
    bytes_downloaded: int = 0
    error: str | None = None


@dataclass(slots=True)
class RemoteBackupReport:
    transport: str
    remote_root: str
    backup_root: str
    items: list[BackupItemReport] = field(default_factory=list)
    manifest_path: str | None = None
    verified: bool = False
    verification_problems: list[str] = field(default_factory=list)


REFERENCE_DIRS = (
    ("wp-content/uploads", "uploads"),
    ("wp-content/themes", "themes"),
    ("wp-content/plugins", "plugins"),
    ("wp-content/mu-plugins", "mu-plugins"),
)

CONFIG_FILES = (
    "wp-config.php",
    ".htaccess",
    ".user.ini",
    "php.ini",
    "robots.txt",
)


def _join(root: str, child: str) -> str:
    return str(PurePosixPath(root) / child)


def _item_from_stats(remote: str, local: Path, kind: str, stats: TransferStats) -> BackupItemReport:
    return BackupItemReport(
        remote_path=remote,
        local_path=str(local),
        kind=kind,
        status="ok",
        files_total=stats.files_total,
        files_downloaded=stats.files_downloaded,
        files_skipped=stats.files_skipped,
        bytes_downloaded=stats.bytes_downloaded,
    )


def backup_wordpress_ftp(
    transport: FTPTransport,
    remote_root: str,
    backup_root: Path,
    *,
    resume: bool = True,
) -> RemoteBackupReport:
    """Back up WordPress user data/reference code over FTP/FTPS.

    Database export is deliberately not attempted here: FTP is a filesystem
    protocol. Database backup belongs to a separate DB adapter (SSH/WP-CLI,
    direct MySQL, or a hosting API).
    """
    backup_root.mkdir(parents=True, exist_ok=True)
    report = RemoteBackupReport(
        transport="ftps" if transport.config.tls else "ftp",
        remote_root=remote_root,
        backup_root=str(backup_root),
    )

    for remote_rel, local_name in REFERENCE_DIRS:
        remote = _join(remote_root, remote_rel)
        local = backup_root / local_name
        try:
            stats = transport.download_tree(remote, local, resume=resume)
            report.items.append(_item_from_stats(remote, local, "directory", stats))
        except error_perm as exc:
            report.items.append(BackupItemReport(
                remote_path=remote,
                local_path=str(local),
                kind="directory",
                status="missing-or-denied",
                error=str(exc),
            ))

    config_dir = backup_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        remote = _join(remote_root, name)
        local = config_dir / name
        try:
            stats = transport.download_file(remote, local, resume=resume)
            report.items.append(_item_from_stats(remote, local, "file", stats))
        except error_perm as exc:
            report.items.append(BackupItemReport(
                remote_path=remote,
                local_path=str(local),
                kind="file",
                status="missing-or-denied",
                error=str(exc),
            ))

    # Write a non-secret transfer report before hashing so it is covered by the manifest.
    report_file = backup_root / "backup-report.json"
    report_file.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    manifest = write_manifest(backup_root)
    ok, problems = verify_manifest(backup_root, manifest)
    report.manifest_path = str(manifest)
    report.verified = ok
    report.verification_problems = problems

    # Rewrite report with final verification state. Manifest intentionally excludes
    # only manifest.json, so regenerate once to cover the finalized report.
    report_file.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = write_manifest(backup_root)
    ok, problems = verify_manifest(backup_root, manifest)
    report.manifest_path = str(manifest)
    report.verified = ok
    report.verification_problems = problems
    return report
