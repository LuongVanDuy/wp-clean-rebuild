from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from .backup import verify_manifest
from .transport import FTPTransport, RemoteFile


ROOT_CORE_FILE = re.compile(r"^(?:index|xmlrpc|wp-[A-Za-z0-9_.-]+)\.php$", re.I)
ROOT_CORE_STATIC = {"license.txt", "readme.html"}
CONFIG_FILES = {"wp-config.php", ".htaccess", ".user.ini", "php.ini", "robots.txt"}
WP_CONTENT_DROPINS = {
    "wp-content/advanced-cache.php",
    "wp-content/object-cache.php",
    "wp-content/db.php",
    "wp-content/sunrise.php",
    "wp-content/install.php",
    "wp-content/maintenance.php",
    "wp-content/index.php",
    "wp-content/debug.log",
}
EPHEMERAL_PREFIXES = (
    "wp-content/cache/",
    "wp-content/languages/",
    "wp-content/upgrade/",
    "wp-content/upgrade-temp-backup/",
    "wp-content/litespeed/",
    "wp-content/wflogs/",
)
BACKED_PREFIXES = {
    "wp-content/uploads/": "uploads",
    "wp-content/themes/": "themes",
    "wp-content/plugins/": "plugins",
    "wp-content/mu-plugins/": "mu-plugins",
}

PreflightProgressCallback = Callable[[dict], None]


@dataclass(slots=True)
class PreflightFile:
    path: str
    size: int | None
    action: str
    reason: str


@dataclass(slots=True)
class RebuildPreflightReport:
    host: str
    remote_root: str
    backup_root: str
    clean_root: str
    original_backup_verified: bool = False
    clean_staging_verified: bool = False
    remote_files: int = 0
    wipe_files: int = 0
    preserve_files: int = 0
    blocked_files: int = 0
    blocked: list[PreflightFile] = field(default_factory=list)
    preserve: list[PreflightFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ready_for_destructive_rebuild: bool = False


def _relative_remote(remote_root: str, path: str) -> str:
    root = PurePosixPath(remote_root)
    item = PurePosixPath(path)
    try:
        return item.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Remote path escaped configured WordPress root: {path}") from exc


def _local_backed_path(backup_root: Path, rel: str) -> Path | None:
    for remote_prefix, local_prefix in BACKED_PREFIXES.items():
        if rel.startswith(remote_prefix):
            child = rel[len(remote_prefix) :]
            return backup_root / local_prefix / Path(*PurePosixPath(child).parts)
    if rel in CONFIG_FILES:
        return backup_root / "config" / rel
    return None


def _classify(remote: RemoteFile, *, remote_root: str, backup_root: Path) -> PreflightFile:
    rel = _relative_remote(remote_root, remote.path)
    lowered = rel.lower()

    # Hosting/ACME validation files are deliberately outside the wipe set.
    if lowered == ".well-known" or lowered.startswith(".well-known/"):
        return PreflightFile(rel, remote.size, "preserve", "hosting/ACME validation path")

    local_backed = _local_backed_path(backup_root, rel)
    if local_backed is not None:
        if not local_backed.is_file():
            return PreflightFile(rel, remote.size, "block", "remote file is missing from the verified backup snapshot")
        local_size = local_backed.stat().st_size
        if remote.size is not None and local_size != remote.size:
            return PreflightFile(
                rel,
                remote.size,
                "block",
                f"remote file drifted after backup (remote={remote.size} bytes, backup={local_size} bytes)",
            )
        return PreflightFile(rel, remote.size, "wipe", "verified backup copy exists")

    first = PurePosixPath(rel).parts[0] if PurePosixPath(rel).parts else rel
    if first in {"wp-admin", "wp-includes"}:
        return PreflightFile(rel, remote.size, "wipe", "WordPress core is reproducible from a trusted clean package")

    if "/" not in rel and (ROOT_CORE_FILE.match(rel) or lowered in ROOT_CORE_STATIC):
        return PreflightFile(rel, remote.size, "wipe", "WordPress root core file is reproducible")

    if lowered == ".maintenance":
        return PreflightFile(rel, remote.size, "wipe", "WordPress maintenance marker")

    if lowered in WP_CONTENT_DROPINS:
        return PreflightFile(rel, remote.size, "wipe", "WordPress drop-in/cache file; reinstall from trusted source if needed")

    if any(lowered.startswith(prefix) for prefix in EPHEMERAL_PREFIXES):
        return PreflightFile(rel, remote.size, "wipe", "reproducible/ephemeral WordPress content")

    return PreflightFile(rel, remote.size, "block", "unknown or non-WordPress file is not covered by the verified backup")


def run_rebuild_preflight(
    *,
    host: str,
    transport: FTPTransport,
    remote_root: str,
    backup_root: Path,
    report_path: Path,
    progress: PreflightProgressCallback | None = None,
) -> RebuildPreflightReport:
    clean_root = backup_root / "clean"
    original_manifest = backup_root / "manifest.json"
    clean_manifest = clean_root / "manifest.json"

    if progress:
        progress({"phase": "verify_original"})
    if not original_manifest.is_file():
        raise ValueError("Original backup manifest.json is missing.")
    original_ok, original_problems = verify_manifest(backup_root, original_manifest)
    if not original_ok:
        raise ValueError("Original backup verification failed: " + "; ".join(original_problems))

    if progress:
        progress({"phase": "verify_clean"})
    if not clean_manifest.is_file():
        raise ValueError("Clean staging manifest.json is missing. Run prepare-clean-config first.")
    clean_ok, clean_problems = verify_manifest(clean_root, clean_manifest)
    if not clean_ok:
        raise ValueError("Clean staging verification failed: " + "; ".join(clean_problems))

    if progress:
        progress({"phase": "inventory_start", "remote_root": remote_root})

    def inventory_progress(event: dict) -> None:
        if not progress:
            return
        normalized = {key: value for key, value in event.items() if key != "phase"}
        progress({"phase": "inventory", "inventory_phase": event.get("phase"), **normalized})

    remote_files = transport.list_files_recursive(remote_root, progress=inventory_progress)

    if progress:
        progress({"phase": "classify", "files_found": len(remote_files)})
    classified = [
        _classify(item, remote_root=remote_root, backup_root=backup_root)
        for item in remote_files
    ]

    blocked = [item for item in classified if item.action == "block"]
    preserve = [item for item in classified if item.action == "preserve"]
    wipe = [item for item in classified if item.action == "wipe"]

    warnings: list[str] = []
    if preserve:
        warnings.append("Preserved paths are excluded from destructive wipe and must be reviewed separately if compromise is suspected.")
    if not transport.config.tls:
        warnings.append("Connection uses plain FTP; credentials and transferred data are not transport-encrypted.")

    report = RebuildPreflightReport(
        host=host,
        remote_root=remote_root,
        backup_root=str(backup_root),
        clean_root=str(clean_root),
        original_backup_verified=True,
        clean_staging_verified=True,
        remote_files=len(classified),
        wipe_files=len(wipe),
        preserve_files=len(preserve),
        blocked_files=len(blocked),
        blocked=blocked,
        preserve=preserve,
        warnings=warnings,
        ready_for_destructive_rebuild=not blocked,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    if progress:
        progress({"phase": "complete", "files_found": len(classified), "blocked_files": len(blocked)})
    return report
