from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from typing import Callable
import zipfile

from .models import Finding, Signal
from .rebuild_execute import _upload_tree
from .risk import severity_for
from .site_config import SiteConnectionProfile
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]
DEFAULT_FLATSOME_PACKAGE = Path(__file__).resolve().parents[2] / "themes" / "flatsome.zip"
PHP_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
TEXT_SUFFIXES = PHP_SUFFIXES | {".js", ".css", ".htaccess", ".txt", ".json", ".xml"}

OBFUSCATION = re.compile(rb"\b(?:base64_decode|gzinflate|gzdecode|str_rot13|hex2bin)\s*\(", re.I)
DYNAMIC_EXECUTION = re.compile(rb"\b(?:eval|assert)\s*\(", re.I)
SYSTEM_EXECUTION = re.compile(rb"\b(?:system|exec|shell_exec|passthru|proc_open|popen)\s*\(", re.I)
REMOTE_IO = re.compile(rb"\b(?:wp_remote_get|wp_remote_post|curl_exec|fsockopen|pfsockopen)\s*\(", re.I)
FILE_MUTATION = re.compile(rb"\b(?:file_put_contents|fwrite|chmod|rename|copy|unlink)\s*\(", re.I)
USER_PERSISTENCE = re.compile(rb"\b(?:wp_create_user|wp_insert_user|wp_update_user|wp_set_password|set_role)\s*\(", re.I)
REQUEST_INPUT = re.compile(rb"\$_(?:GET|POST|REQUEST|COOKIE)\s*\[", re.I)
LONG_ENCODED_BLOB = re.compile(rb"[A-Za-z0-9+/]{700,}={0,2}")
JS_DYNAMIC = re.compile(rb"\b(?:eval\s*\(|Function\s*\(|atob\s*\()", re.I)


@dataclass(slots=True)
class ActiveTheme:
    template: str
    stylesheet: str

    @property
    def is_flatsome(self) -> bool:
        return self.template.lower() == "flatsome"

    @property
    def has_child(self) -> bool:
        return self.is_flatsome and self.stylesheet.lower() != "flatsome"


@dataclass(slots=True)
class ChildThemeScan:
    slug: str
    path: str
    files_scanned: int = 0
    unreadable_files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.unreadable_files) or any(item.score >= 60 for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        findings: list[dict[str, object]] = []
        for item in self.findings:
            findings.append(
                {
                    "source": item.source,
                    "location": item.location,
                    "score": item.score,
                    "severity": item.severity.value,
                    "signals": [asdict(signal) for signal in item.signals],
                    "preview": item.preview,
                    "metadata": item.metadata,
                    "action": item.recommended_action,
                }
            )
        return {
            "slug": self.slug,
            "path": self.path,
            "files_scanned": self.files_scanned,
            "unreadable_files": self.unreadable_files,
            "blocked": self.blocked,
            "findings": findings,
        }


@dataclass(slots=True)
class ThemeStageResult:
    template: str = ""
    stylesheet: str = ""
    mode: str = "unknown"
    flatsome_prompted: bool = False
    flatsome_installed: bool = False
    flatsome_files_uploaded: int = 0
    flatsome_package_sha256: str = ""
    child_theme_detected: bool = False
    child_theme_slug: str = ""
    child_scan: dict[str, object] | None = None
    child_prompted: bool = False
    child_installed: bool = False
    child_files_uploaded: int = 0
    unsupported_theme: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _sql_unescape(value: str) -> str:
    return value.replace("\\'", "'").replace("\\\\", "\\")


def _find_option_value(sql: str, option_name: str) -> str | None:
    pattern = re.compile(
        rf"\(\s*\d+\s*,\s*'{re.escape(option_name)}'\s*,\s*'((?:\\.|[^'])*)'\s*,",
        re.I,
    )
    matches = pattern.findall(sql)
    if matches:
        return _sql_unescape(matches[-1])

    fallback = re.compile(
        rf"\(\s*'{re.escape(option_name)}'\s*,\s*'((?:\\.|[^'])*)'\s*,",
        re.I,
    )
    matches = fallback.findall(sql)
    return _sql_unescape(matches[-1]) if matches else None


def detect_active_theme(clean_sql: Path) -> ActiveTheme | None:
    if not clean_sql.is_file() or clean_sql.stat().st_size == 0:
        return None
    sql = clean_sql.read_text(encoding="utf-8", errors="replace")
    template = _find_option_value(sql, "template")
    stylesheet = _find_option_value(sql, "stylesheet")
    if not template or not stylesheet:
        return None
    return ActiveTheme(template=template.strip(), stylesheet=stylesheet.strip())


def _read_theme_header(style_css: Path) -> dict[str, str]:
    try:
        text = style_css.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        return {}
    headers: dict[str, str] = {}
    for key in ("Theme Name", "Template", "Version"):
        match = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text, re.I | re.M)
        if match:
            headers[key.lower().replace(" ", "_")] = match.group(1).strip()
    return headers


def _score_child_file(path: Path, data: bytes) -> Finding | None:
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return None

    score = 0
    signals: list[Signal] = []

    if suffix in PHP_SUFFIXES:
        obfuscation = bool(OBFUSCATION.search(data))
        dynamic = bool(DYNAMIC_EXECUTION.search(data))
        system_exec = bool(SYSTEM_EXECUTION.search(data))
        remote = bool(REMOTE_IO.search(data))
        mutation = bool(FILE_MUTATION.search(data))
        persistence = bool(USER_PERSISTENCE.search(data))
        request_input = bool(REQUEST_INPUT.search(data))
        encoded_blob = bool(LONG_ENCODED_BLOB.search(data))

        if obfuscation:
            score += 30
            signals.append(Signal("theme.obfuscation", 30, "Encoded/compressed payload decoder found in child-theme PHP."))
        if dynamic:
            score += 45
            signals.append(Signal("theme.dynamic_execution", 45, "Dynamic PHP execution primitive found."))
        if system_exec:
            score += 55
            signals.append(Signal("theme.system_execution", 55, "Operating-system command execution primitive found."))
        if persistence:
            score += 35
            signals.append(Signal("theme.user_persistence", 35, "WordPress user/account mutation found."))
        if remote:
            score += 20
            signals.append(Signal("theme.remote_io", 20, "Remote/network I/O primitive found."))
        if mutation:
            score += 20
            signals.append(Signal("theme.file_mutation", 20, "Filesystem mutation primitive found."))
        if request_input:
            score += 15
            signals.append(Signal("theme.request_input", 15, "Direct request-controlled input is used in PHP."))
        if encoded_blob:
            score += 30
            signals.append(Signal("theme.long_encoded_blob", 30, "Large encoded blob found in PHP source."))

        if obfuscation and (dynamic or system_exec):
            score += 30
        if request_input and (dynamic or system_exec or mutation):
            score += 25
        if remote and mutation and (obfuscation or request_input):
            score += 25

    elif suffix == ".js" and JS_DYNAMIC.search(data) and LONG_ENCODED_BLOB.search(data):
        score = 70
        signals.append(Signal("theme.obfuscated_javascript", 70, "Obfuscated/dynamic JavaScript payload found."))

    score = min(score, 100)
    if score < 30:
        return None

    digest = hashlib.sha256(data).hexdigest()
    preview = " ".join(data[:500].decode("utf-8", errors="replace").split())
    return Finding(
        source="theme-child",
        location=str(path),
        score=score,
        severity=severity_for(score),
        signals=signals,
        preview=preview[:300],
        metadata={"sha256": digest, "size": len(data)},
        action_override="BLOCK CHILD THEME RESTORE" if score >= 60 else None,
    )


def _child_theme_backup_exclusions(backup_root: Path, slug: str) -> list[str]:
    report_path = backup_root / "backup-report.json"
    if not report_path.is_file():
        return []
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"BACKUP REPORT UNREADABLE: {report_path}"]

    needle = f"/wp-content/themes/{slug.lower()}/"
    exclusions: list[str] = []
    for item in payload.get("exclusions", []):
        if str(item.get("stage", "")).lower() != "themes":
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        normalized = "/" + path.lstrip("/").lower()
        if needle in normalized:
            exclusions.append(f"BACKUP EXCLUDED: {path}")
    return exclusions


def scan_child_theme(
    theme_root: Path,
    *,
    slug: str | None = None,
    backup_root: Path | None = None,
) -> ChildThemeScan:
    slug = slug or theme_root.name
    report = ChildThemeScan(slug=slug, path=str(theme_root))
    if backup_root is not None:
        report.unreadable_files.extend(_child_theme_backup_exclusions(backup_root, slug))
    if not theme_root.is_dir():
        report.unreadable_files.append(str(theme_root))
        return report

    style_css = theme_root / "style.css"
    headers = _read_theme_header(style_css)
    if not style_css.is_file() or headers.get("template", "").lower() != "flatsome":
        report.findings.append(
            Finding(
                source="theme-child",
                location=str(style_css),
                score=80,
                severity=severity_for(80),
                signals=[Signal("theme.invalid_child_header", 80, "Theme does not declare Template: flatsome in style.css.")],
                action_override="BLOCK CHILD THEME RESTORE",
            )
        )
        return report

    for path in theme_root.rglob("*"):
        if not path.is_file():
            continue
        report.files_scanned += 1
        try:
            if path.stat().st_size > 8 * 1024 * 1024 and path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            data = path.read_bytes()
        except OSError:
            report.unreadable_files.append(str(path))
            continue
        finding = _score_child_file(path, data)
        if finding:
            report.findings.append(finding)
    return report


def _safe_extract_flatsome(package: Path, destination: Path) -> tuple[Path, str]:
    if not package.is_file() or package.stat().st_size == 0:
        raise ValueError(f"Trusted Flatsome package is missing or empty: {package}")

    digest = hashlib.sha256()
    with package.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)

    try:
        with zipfile.ZipFile(package) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if not members:
                raise ValueError("Flatsome ZIP is empty.")

            roots = {
                PurePosixPath(item.filename.replace("\\", "/")).parts[0]
                for item in members
                if PurePosixPath(item.filename.replace("\\", "/")).parts
            }
            by_lower = {item.lower(): item for item in roots}
            candidate_root = by_lower.get("flatsome") or (next(iter(roots)) if len(roots) == 1 else "")
            if not candidate_root:
                raise ValueError("Flatsome ZIP must contain one installable theme root.")

            for member in archive.infolist():
                normalized = PurePosixPath(member.filename.replace("\\", "/"))
                if not normalized.parts or normalized.parts[0] != candidate_root:
                    continue
                rel = PurePosixPath(*normalized.parts[1:])
                if not rel.parts:
                    continue
                target = destination.joinpath(*rel.parts)
                resolved = target.resolve()
                dest_resolved = destination.resolve()
                if dest_resolved not in resolved.parents and resolved != dest_resolved:
                    raise ValueError(f"Unsafe path in Flatsome ZIP: {member.filename}")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as src, target.open("wb") as dst:
                        while chunk := src.read(1024 * 1024):
                            dst.write(chunk)
    except zipfile.BadZipFile as exc:
        raise ValueError("Trusted Flatsome package is not a valid ZIP archive.") from exc

    style_css = destination / "style.css"
    headers = _read_theme_header(style_css)
    if not style_css.is_file() or "flatsome" not in headers.get("theme_name", "").lower():
        raise ValueError("Flatsome ZIP validation failed: style.css does not identify Flatsome.")
    return destination, digest.hexdigest()


def install_flatsome(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    *,
    package: Path = DEFAULT_FLATSOME_PACKAGE,
    progress: ProgressCallback | None = None,
) -> tuple[int, str]:
    with TemporaryDirectory(prefix="wpclean-flatsome-") as temp_dir:
        theme_root, digest = _safe_extract_flatsome(package, Path(temp_dir))
        remote = str(PurePosixPath(profile.remote_path) / "wp-content" / "themes" / "flatsome")
        uploaded = _upload_tree(
            transport,
            theme_root,
            remote,
            progress_phase="upload_flatsome_theme",
            progress=progress,
        )
    return uploaded, digest


def install_child_theme(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    theme_root: Path,
    slug: str,
    *,
    progress: ProgressCallback | None = None,
) -> int:
    remote = str(PurePosixPath(profile.remote_path) / "wp-content" / "themes" / slug)
    return _upload_tree(
        transport,
        theme_root,
        remote,
        progress_phase="upload_child_theme",
        progress=progress,
    )


def plan_theme_stage(backup_root: Path) -> tuple[ActiveTheme | None, Path | None]:
    clean_sql = backup_root / "clean" / "database" / "clean.sql"
    active = detect_active_theme(clean_sql)
    if active is None:
        return None, None
    child_root = backup_root / "themes" / active.stylesheet if active.has_child else None
    return active, child_root


__all__ = [
    "ActiveTheme",
    "ChildThemeScan",
    "ThemeStageResult",
    "DEFAULT_FLATSOME_PACKAGE",
    "detect_active_theme",
    "scan_child_theme",
    "install_flatsome",
    "install_child_theme",
    "plan_theme_stage",
]
