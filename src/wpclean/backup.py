from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


@dataclass(slots=True)
class BackupManifest:
    created_at: str
    root: str
    entries: list[ManifestEntry]


@dataclass(slots=True)
class BackupCompleteness:
    complete: bool
    problems: list[str]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> BackupManifest:
    entries: list[ManifestEntry] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        entries.append(ManifestEntry(
            path=path.relative_to(root).as_posix(),
            size=path.stat().st_size,
            sha256=sha256_file(path),
        ))
    return BackupManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        root=str(root.resolve()),
        entries=entries,
    )


def write_manifest(root: Path) -> Path:
    manifest = build_manifest(root)
    out = root / "manifest.json"
    out.write_text(json.dumps(asdict(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def verify_manifest(root: Path, manifest_path: Path | None = None) -> tuple[bool, list[str]]:
    """Verify file integrity and, for full backups, minimum completeness.

    Files explicitly marked ``ok-with-exclusions`` in backup-report.json are not
    expected to exist locally and are intentionally never restored. Whole-stage
    failures and required artifact failures still make the recovery set incomplete.
    """
    manifest_path = manifest_path or root / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in raw.get("entries", []):
        path = root / entry["path"]
        if not path.exists():
            problems.append(f"missing: {entry['path']}")
            continue
        if path.stat().st_size != entry["size"]:
            problems.append(f"size mismatch: {entry['path']}")
            continue
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            problems.append(f"hash mismatch: {entry['path']}")

    if (root / "database" / "original.sql").is_file():
        completeness = verify_backup_completeness(root, require_database=True)
        problems.extend(f"incomplete: {problem}" for problem in completeness.problems)

    return (not problems, problems)


def verify_backup_completeness(root: Path, *, require_database: bool = True) -> BackupCompleteness:
    """Check whether a backup contains the minimum artifacts needed for a safe rebuild."""
    problems: list[str] = []

    if require_database:
        database = root / "database" / "original.sql"
        if not database.is_file() or database.stat().st_size == 0:
            problems.append("database/original.sql is missing or empty")

    uploads = root / "uploads"
    legacy_uploads = root / "wp-content" / "uploads"
    if not uploads.is_dir() and not legacy_uploads.is_dir():
        problems.append("uploads backup directory is missing")

    config = root / "config" / "wp-config.php"
    if not config.is_file() or config.stat().st_size == 0:
        problems.append("config/wp-config.php is missing or empty")

    report_path = root / "backup-report.json"
    if not report_path.is_file():
        problems.append("backup-report.json is missing")
    else:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            for item in report.get("items", []):
                remote = str(item.get("remote_path", ""))
                status = str(item.get("status", ""))
                optional = (
                    remote.endswith("/wp-content/mu-plugins")
                    or remote.endswith("/php.ini")
                    or remote.endswith("/.user.ini")
                    or remote.endswith("/robots.txt")
                )
                # A directory can be complete enough for clean rebuild while
                # explicitly excluding individual unreadable files. Those files
                # are recorded in the report and are never restored.
                accepted = status in {"ok", "ok-with-exclusions"}
                if not accepted and not optional:
                    problems.append(
                        f"backup stage incomplete: {remote or 'unknown'} ({status or 'unknown'})"
                    )
        except Exception as exc:
            problems.append(f"backup-report.json could not be parsed: {exc}")

    return BackupCompleteness(complete=not problems, problems=problems)
