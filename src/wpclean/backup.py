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
    return (not problems, problems)
