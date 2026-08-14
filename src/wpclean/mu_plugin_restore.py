from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Callable

from .rebuild_execute import _upload_file, _upload_tree
from .site_config import SiteConnectionProfile
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]
PHP_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
TEXT_SUFFIXES = PHP_SUFFIXES | {".js", ".css", ".htaccess", ".txt", ".json", ".xml"}
MAX_SCAN_BYTES = 16 * 1024 * 1024

OBFUSCATION = re.compile(rb"\b(?:base64_decode|gzinflate|gzdecode|str_rot13|hex2bin)\s*\(", re.I)
DYNAMIC_EXECUTION = re.compile(rb"\b(?:eval|assert)\s*\(", re.I)
SYSTEM_EXECUTION = re.compile(rb"\b(?:system|exec|shell_exec|passthru|proc_open|popen)\s*\(", re.I)
REMOTE_IO = re.compile(rb"\b(?:wp_remote_get|wp_remote_post|curl_exec|fsockopen|pfsockopen)\s*\(", re.I)
FILE_MUTATION = re.compile(rb"\b(?:file_put_contents|fwrite|chmod|rename|copy|unlink)\s*\(", re.I)
USER_PERSISTENCE = re.compile(rb"\b(?:wp_create_user|wp_insert_user|wp_update_user|wp_set_password|set_role)\s*\(", re.I)
REQUEST_INPUT = re.compile(rb"\$_(?:GET|POST|REQUEST|COOKIE)\s*\[", re.I)
LONG_ENCODED_BLOB = re.compile(rb"[A-Za-z0-9+/]{700,}={0,2}")
JS_DYNAMIC = re.compile(rb"\b(?:eval\s*\(|Function\s*\(|atob\s*\()", re.I)
KNOWN_MALWARE_MARKERS = (
    b"vivid-toolkit-tap",
    b"plugin name: bold recorder bit",
    b"sc_th_begin",
    b"oi05awbus3",
)


@dataclass(slots=True)
class MuPluginFinding:
    component: str
    path: str
    score: int
    severity: str
    reasons: list[str] = field(default_factory=list)
    sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class MuPluginComponent:
    name: str
    kind: str
    files_scanned: int = 0
    files_total: int = 0
    blocked: bool = False
    unreadable_files: list[str] = field(default_factory=list)
    findings: list[MuPluginFinding] = field(default_factory=list)
    files_uploaded: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "files_scanned": self.files_scanned,
            "files_total": self.files_total,
            "blocked": self.blocked,
            "unreadable_files": self.unreadable_files,
            "findings": [item.to_dict() for item in self.findings],
            "files_uploaded": self.files_uploaded,
        }


@dataclass(slots=True)
class MuPluginStageReport:
    source_path: str = ""
    inventory_count: int = 0
    files_scanned: int = 0
    clean_components: int = 0
    blocked_components: int = 0
    files_uploaded: int = 0
    components: list[MuPluginComponent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "inventory_count": self.inventory_count,
            "files_scanned": self.files_scanned,
            "clean_components": self.clean_components,
            "blocked_components": self.blocked_components,
            "files_uploaded": self.files_uploaded,
            "components": [item.to_dict() for item in self.components],
            "warnings": self.warnings,
            "completed": self.completed,
        }


def _severity(score: int) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def _score_file(component: str, path: Path, data: bytes) -> MuPluginFinding | None:
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return None

    lowered = data.lower()
    matched_markers = [marker.decode("ascii", errors="replace") for marker in KNOWN_MALWARE_MARKERS if marker in lowered]
    if matched_markers:
        return MuPluginFinding(
            component=component,
            path=str(path),
            score=100,
            severity="CRITICAL",
            reasons=["Phát hiện marker malware đã biết: " + ", ".join(matched_markers)],
            sha256=hashlib.sha256(data).hexdigest(),
        )

    score = 0
    reasons: list[str] = []
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
            reasons.append("Có hàm giải mã/nén payload đáng ngờ")
        if dynamic:
            score += 45
            reasons.append("Có thực thi PHP động eval/assert")
        if system_exec:
            score += 55
            reasons.append("Có hàm thực thi lệnh hệ điều hành")
        if persistence:
            score += 35
            reasons.append("Có thao tác tạo/sửa tài khoản WordPress")
        if remote:
            score += 20
            reasons.append("Có thao tác kết nối ra ngoài")
        if mutation:
            score += 20
            reasons.append("Có thao tác ghi/sửa/xóa file")
        if request_input:
            score += 15
            reasons.append("Có sử dụng trực tiếp dữ liệu request")
        if encoded_blob:
            score += 30
            reasons.append("Có chuỗi encoded dài bất thường")

        if obfuscation and (dynamic or system_exec):
            score += 30
        if request_input and (dynamic or system_exec or mutation):
            score += 25
        if remote and mutation and (obfuscation or request_input):
            score += 25
    elif suffix == ".js" and JS_DYNAMIC.search(data) and LONG_ENCODED_BLOB.search(data):
        score = 70
        reasons.append("JavaScript obfuscation/dynamic payload đáng ngờ")

    score = min(score, 100)
    if score < 30:
        return None
    return MuPluginFinding(
        component=component,
        path=str(path),
        score=score,
        severity=_severity(score),
        reasons=reasons,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _component_files(component_path: Path) -> list[Path]:
    if component_path.is_file() or component_path.is_symlink():
        return [component_path]
    return sorted(path for path in component_path.rglob("*") if path.is_file() or path.is_symlink())


def _backup_exclusions(backup_root: Path) -> list[str]:
    report_path = backup_root / "backup-report.json"
    if not report_path.is_file():
        return []
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["BACKUP REPORT UNREADABLE"]
    result: list[str] = []
    for item in payload.get("exclusions", []):
        if str(item.get("stage", "")).lower() != "mu-plugins":
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        if path:
            result.append(path)
    return result


def _exclusions_for_component(exclusions: list[str], component: str) -> list[str]:
    component_lower = component.lower()
    needle_dir = f"/wp-content/mu-plugins/{component_lower}/"
    needle_file = f"/wp-content/mu-plugins/{component_lower}"
    matched: list[str] = []
    for raw in exclusions:
        normalized = "/" + raw.lstrip("/").lower()
        if normalized == needle_file or needle_dir in normalized:
            matched.append(raw)
    return matched


def scan_mu_plugins(backup_root: Path) -> MuPluginStageReport:
    source = backup_root / "mu-plugins"
    report = MuPluginStageReport(source_path=str(source))
    if not source.exists():
        report.completed = True
        report.warnings.append("Backup không có thư mục mu-plugins; không có gì để restore.")
        return report
    if not source.is_dir():
        report.warnings.append(f"Đường dẫn mu-plugins trong backup không phải thư mục: {source}")
        return report

    exclusions = _backup_exclusions(backup_root)
    components = sorted(source.iterdir(), key=lambda item: item.name.lower())
    report.inventory_count = len(components)

    for component_path in components:
        component = MuPluginComponent(
            name=component_path.name,
            kind="directory" if component_path.is_dir() and not component_path.is_symlink() else "file",
        )
        excluded = _exclusions_for_component(exclusions, component.name)
        if excluded:
            component.unreadable_files.extend(f"BACKUP EXCLUDED: {item}" for item in excluded)
            component.blocked = True

        files = _component_files(component_path)
        component.files_total = len(files)
        for path in files:
            rel = str(path.relative_to(source)) if path.exists() or path.is_symlink() else str(path)
            if path.is_symlink():
                component.unreadable_files.append(f"SYMLINK KHÔNG ĐƯỢC RESTORE: {rel}")
                component.blocked = True
                continue
            try:
                size = path.stat().st_size
                if path.suffix.lower() in TEXT_SUFFIXES:
                    if size > MAX_SCAN_BYTES:
                        component.unreadable_files.append(
                            f"FILE CODE QUÁ LỚN ĐỂ SCAN AN TOÀN ({size} bytes): {rel}"
                        )
                        component.blocked = True
                        continue
                    data = path.read_bytes()
                    component.files_scanned += 1
                    report.files_scanned += 1
                    finding = _score_file(component.name, path, data)
                    if finding is not None:
                        component.findings.append(finding)
                        if finding.score >= 60:
                            component.blocked = True
            except OSError as exc:
                component.unreadable_files.append(f"{rel}: {type(exc).__name__}: {exc}")
                component.blocked = True

        if component.blocked:
            report.blocked_components += 1
        else:
            report.clean_components += 1
        report.components.append(component)

    report.completed = True
    return report


def _persist_report(report_path: Path, stage: MuPluginStageReport) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["mu_plugin_stage"] = stage.to_dict()
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    standalone = report_path.with_name("mu-plugin-stage.json")
    standalone.write_text(json.dumps(stage.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return standalone


def run_mu_plugin_stage(
    *,
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    backup_root: Path,
    report_path: Path,
    progress: ProgressCallback | None = None,
) -> MuPluginStageReport:
    stage = scan_mu_plugins(backup_root)
    source = Path(stage.source_path)

    if not source.exists():
        _persist_report(report_path, stage)
        return stage

    remote_base = str(PurePosixPath(profile.remote_path) / "wp-content" / "mu-plugins")
    for component in stage.components:
        component_path = source / component.name
        if component.blocked:
            continue
        try:
            if component.kind == "directory":
                uploaded = _upload_tree(
                    transport,
                    component_path,
                    str(PurePosixPath(remote_base) / component.name),
                    progress_phase="upload_mu_plugin",
                    progress=progress,
                )
            else:
                remote_path = str(PurePosixPath(remote_base) / component.name)
                if progress:
                    progress({"phase": "upload_mu_plugin", "component": component.name, "current": remote_path})
                _upload_file(transport, remote_path, component_path)
                uploaded = 1
            component.files_uploaded = uploaded
            stage.files_uploaded += uploaded
        except Exception as exc:
            component.blocked = True
            stage.clean_components = max(0, stage.clean_components - 1)
            stage.blocked_components += 1
            component.unreadable_files.append(f"UPLOAD FAILED: {type(exc).__name__}: {exc}")
            stage.warnings.append(f"Upload MU-plugin component {component.name} thất bại: {type(exc).__name__}: {exc}")

    _persist_report(report_path, stage)
    return stage


__all__ = ["MuPluginStageReport", "run_mu_plugin_stage", "scan_mu_plugins"]
