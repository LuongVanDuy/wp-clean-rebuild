from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse
import json
import hashlib
import os
import re
import secrets
import threading
import time
import traceback
import webbrowser

import typer

from .backup import verify_manifest, write_manifest
from .clean_builder import build_clean_restore
from .db_bridge import export_database_via_php_bridge
from .gui_observability import classify_exception, error_payload, job_timing
from .mu_plugin_restore import run_mu_plugin_stage
from .rebuild_execute import execute_rebuild
from .rebuild_preflight import run_rebuild_preflight
from .rebuild_resume import resume_database_import
from .remote_backup import backup_wordpress_ftp
from .scanners import scan_sql, scan_uploads
from .site_config import SiteConnectionProfile, load_site_profile
from .theme_restore import plan_theme_stage
from . import operator_entry as operator
from . import operator_wizard as wizard
from . import plugin_workflow
from . import rebuild_entry


PROJECT_ROOT = Path.cwd().resolve()
SITES_DIR = PROJECT_ROOT / "sites"
REPORTS_DIR = PROJECT_ROOT / "reports"
BACKUPS_DIR = PROJECT_ROOT / "backups"
REPAIRS_DIR = PROJECT_ROOT / "repairs"
TOKEN = secrets.token_urlsafe(32)
PROJECT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _secret_fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


@dataclass
class GuiJob:
    project: str
    status: str = "idle"  # idle | running | needs-action | success | error | paused
    stage: str = ""
    title: str = ""
    message: str = ""
    percent: int = 0
    current: str = ""
    completed_items: int = 0
    total_items: int = 0
    bytes_completed: int = 0
    bytes_total: int = 0
    bytes_per_second: float = 0.0
    failed_items: int = 0
    retry_attempt: int = 0
    retry_limit: int = 0
    progress_phase: str = ""
    progress_unit: str = "file"
    error: str = ""
    error_code: str = ""
    error_title: str = ""
    recovery: str = ""
    retryable: bool = True
    technical_error: str = ""
    decision: dict[str, Any] | None = None
    logs: list[str] = field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""
    sequence: int = 0

    def touch(self) -> None:
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.sequence += 1

    def set(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        title: str | None = None,
        message: str | None = None,
        percent: int | None = None,
        current: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        error_title: str | None = None,
        recovery: str | None = None,
        retryable: bool | None = None,
        technical_error: str | None = None,
        decision: dict[str, Any] | None | object = ...,
    ) -> None:
        if status is not None:
            self.status = status
        if stage is not None and stage != self.stage:
            self.completed_items = 0
            self.total_items = 0
            self.bytes_completed = 0
            self.bytes_total = 0
            self.bytes_per_second = 0.0
            self.failed_items = 0
            self.retry_attempt = 0
            self.retry_limit = 0
            self.progress_phase = ""
            self.progress_unit = "file"
        if stage is not None:
            self.stage = stage
        if title is not None:
            self.title = title
        if message is not None:
            self.message = message
        if percent is not None:
            self.percent = max(0, min(100, int(percent)))
        if current is not None:
            self.current = current
        if error is not None:
            self.error = error
        if error_code is not None:
            self.error_code = error_code
        if error_title is not None:
            self.error_title = error_title
        if recovery is not None:
            self.recovery = recovery
        if retryable is not None:
            self.retryable = bool(retryable)
        if technical_error is not None:
            self.technical_error = technical_error
        if decision is not ...:
            self.decision = decision  # type: ignore[assignment]
        self.touch()

    def log(
        self,
        text: str,
        *,
        level: str = "",
        code: str = "",
        title: str = "",
        recovery: str = "",
        technical: str = "",
    ) -> None:
        """Store concise session output and persist it when a journal is attached."""

        raw_text = str(text)
        secrets_to_hide = getattr(self, "_journal_secrets", ())
        for secret in secrets_to_hide:
            value = str(secret or "")
            if len(value) >= 3:
                raw_text = raw_text.replace(value, "***")
        clean = ANSI_RE.sub("", raw_text).replace("\r", "\n").strip()
        if not clean:
            return
        if clean.startswith("Traceback (most recent call last):"):
            trace_lines = [line.strip() for line in clean.splitlines() if line.strip()]
            clean = "ERROR | " + (trace_lines[-1] if trace_lines else "Lỗi Python không xác định")

        journal_dir = getattr(self, "_journal_dir", None)
        event_level = level or ("error" if clean.startswith("ERROR |") else "info")
        for raw in clean.splitlines():
            line = raw.strip()
            if not line:
                continue
            display = line
            if not re.match(r"^\[\d{2}:\d{2}:\d{2}\]", display):
                display = f"[{datetime.now().strftime('%H:%M:%S')}] {display}"
            self.logs.append(display)
            if journal_dir is not None:
                from .project_journal import append_activity

                append_activity(
                    journal_dir,
                    project=self.project,
                    stage=self.stage,
                    level=event_level,
                    session_id=getattr(self, "_journal_session", ""),
                    message=line,
                    secrets=secrets_to_hide,
                    code=code,
                    title=title,
                    recovery=recovery,
                    technical=technical,
                )
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]
        self.touch()

    def fail(self, exc: BaseException, *, technical: str = "") -> None:
        info = classify_exception(exc, stage=self.stage)
        detail = technical or info.technical
        message = info.message
        for secret in getattr(self, "_journal_secrets", ()):
            value = str(secret or "")
            if len(value) >= 3:
                detail = detail.replace(value, "***")
                message = message.replace(value, "***")
        self.log(
            f"ERROR | [{info.code}] {info.title} · {message}",
            level="error",
            code=info.code,
            title=info.title,
            recovery=info.recovery,
            technical=detail,
        )
        self.set(
            status="error",
            title=info.title,
            message=message,
            error=message,
            error_code=info.code,
            error_title=info.title,
            recovery=info.recovery,
            retryable=info.retryable,
            technical_error=detail,
            decision=None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "project": self.project,
            "status": self.status,
            "stage": self.stage,
            "title": self.title,
            "message": self.message,
            "percent": self.percent,
            "current": self.current,
            "progress": {
                "completed": self.completed_items,
                "total": self.total_items,
                "bytesCompleted": self.bytes_completed,
                "bytesTotal": self.bytes_total,
                "bytesPerSecond": self.bytes_per_second,
                "failed": self.failed_items,
                "retryAttempt": self.retry_attempt,
                "retryLimit": self.retry_limit,
                "phase": self.progress_phase,
                "unit": self.progress_unit,
            },
            "error": self.error,
            "errorCode": self.error_code,
            "errorTitle": self.error_title,
            "recovery": self.recovery,
            "retryable": self.retryable,
            "technicalError": self.technical_error,
            "decision": self.decision,
            "logs": self.logs[-300:],
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "sequence": self.sequence,
        }
        payload.update(
            job_timing(
                started_at=self.started_at,
                updated_at=self.updated_at,
                status=self.status,
            )
        )
        payload["errorInfo"] = (
            {
                "code": self.error_code,
                "title": self.error_title or self.title,
                "message": self.error,
                "recovery": self.recovery,
                "retryable": self.retryable,
                "technical": self.technical_error,
            }
            if self.error or self.error_code
            else None
        )
        return payload


JOBS: dict[str, GuiJob] = {}
JOBS_LOCK = threading.Lock()
ACTIVE_PROJECT: str | None = None


def _json_read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_project_path(name: str) -> Path:
    name = unquote(name).strip()
    if not PROJECT_RE.fullmatch(name):
        raise ValueError("Tên dự án không hợp lệ.")
    path = (SITES_DIR / f"{name}.json").resolve()
    path.relative_to(SITES_DIR.resolve())
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy dự án: {name}")
    return path


def _profile_and_paths(name: str) -> tuple[Path, SiteConnectionProfile, dict[str, Path]]:
    profile_path = _safe_project_path(name)
    profile = load_site_profile(profile_path)
    paths = wizard._paths(profile, profile_path)
    return profile_path, profile, paths


def _transport(profile: SiteConnectionProfile):
    if not profile.password:
        raise ValueError("Dự án chưa có mật khẩu FTP trong cấu hình local.")
    return wizard._transport(profile, profile.password)


def _step_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    final_ok = status.get("final_status") in {"PASS", "PASS WITH WARNINGS"}
    manual_ok = not status.get("plugin_manual") or status.get("manual_plugins_ack")
    return [
        {"key": "backup-files", "label": "Backup file", "done": bool(status.get("filesystem_backup"))},
        {"key": "backup-db", "label": "Backup database", "done": bool(status.get("database_backup"))},
        {"key": "verify-scan", "label": "Kiểm tra & quét", "done": bool(status.get("scan_ready"))},
        {"key": "clean", "label": "Tạo dữ liệu sạch", "done": bool(status.get("clean_ready"))},
        {"key": "preflight", "label": "Kiểm tra an toàn", "done": bool(status.get("preflight_ready"))},
        {"key": "rebuild", "label": "Rebuild WordPress", "done": bool(status.get("rebuild_ready"))},
        {"key": "theme", "label": "Theme", "done": bool(status.get("theme_done"))},
        {"key": "plugin", "label": "Plugin", "done": bool(status.get("plugin_done"))},
        {"key": "mu-plugin", "label": "MU-plugin", "done": bool(status.get("mu_plugin_done"))},
        {"key": "manual-plugins", "label": "Plugin thủ công", "done": bool(manual_ok)},
        {"key": "final", "label": "Kiểm tra website", "done": bool(final_ok)},
    ]


def _project_payload(name: str) -> dict[str, Any]:
    profile_path, profile, paths = _profile_and_paths(name)
    status = operator._infer_status(paths)
    next_stage = operator._next_stage(status)
    job = JOBS.get(name)
    execution = _json_read(paths["execute"])
    theme_stage = execution.get("theme_stage") if isinstance(execution.get("theme_stage"), dict) else {}
    plugin_stage = execution.get("plugin_stage") if isinstance(execution.get("plugin_stage"), dict) else {}
    mu_stage = execution.get("mu_plugin_stage") if isinstance(execution.get("mu_plugin_stage"), dict) else {}
    return {
        "name": name,
        "host": profile.host,
        "url": profile.web_base_url,
        "adminUrl": profile.web_base_url.rstrip("/") + "/wp-admin/",
        "protocol": profile.protocol.upper(),
        "remotePath": profile.remote_path,
        "workers": profile.workers,
        "backupRoot": str(paths["backup"]),
        "nextStage": next_stage,
        "nextLabel": operator.wizard.STAGE_LABELS.get(next_stage, next_stage),
        "completed": status.get("final_status") in {"PASS", "PASS WITH WARNINGS"},
        "finalStatus": status.get("final_status") or "",
        "themeRepair": str(theme_stage.get("child_repair_workspace") or ""),
        "theme": theme_stage,
        "plugin": plugin_stage,
        "muPlugin": mu_stage,
        "steps": _step_rows(status),
        "job": job.to_dict() if job else None,
        "profileFile": str(profile_path),
    }


def list_projects() -> list[dict[str, Any]]:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(SITES_DIR.glob("*.json"), key=lambda p: p.name.lower()):
        if path.name.endswith(".example.json") or path.name.endswith(".local.json"):
            continue
        try:
            items.append(_project_payload(path.stem))
        except Exception as exc:
            items.append(
                {
                    "name": path.stem,
                    "host": path.stem,
                    "url": "",
                    "protocol": "?",
                    "remotePath": "?",
                    "nextStage": "config-error",
                    "nextLabel": "Cấu hình lỗi",
                    "completed": False,
                    "finalStatus": "",
                    "steps": [],
                    "job": None,
                    "error": str(exc),
                }
            )
    return items


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    host = str(data.get("host") or "").strip()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    protocol = str(data.get("protocol") or "ftp").strip().lower()
    site_url = str(data.get("siteUrl") or f"https://{host}").strip()
    if not host or not username or not password:
        raise ValueError("Host, tài khoản FTP và mật khẩu là bắt buộc.")
    if protocol not in {"ftp", "ftps", "ftp+tls", "ftp-tls"}:
        raise ValueError("Giao thức chỉ hỗ trợ FTP hoặc FTPS.")
    project_name = wizard._slug(str(data.get("name") or host))
    if not PROJECT_RE.fullmatch(project_name):
        raise ValueError("Tên dự án không hợp lệ.")
    path = SITES_DIR / f"{project_name}.json"
    if path.exists():
        raise FileExistsError(f"Dự án {project_name} đã tồn tại.")
    port = int(data.get("port") or (21 if protocol == "ftp" else 21))
    workers = max(1, min(16, int(data.get("workers") or 4)))
    block_mb = max(1, min(8, int(data.get("blockMb") or 1)))
    remote_path = str(data.get("remotePath") or f"/domains/{host}/public_html").strip()
    payload = {
        "host": host,
        "username": username,
        "password": password,
        "protocol": protocol,
        "port": port,
        "remotePath": remote_path,
        "siteUrl": site_url,
        "passive": bool(data.get("passive", True)),
        "workers": workers,
        "blockMb": block_mb,
        "initialFtpPasswordFingerprint": _secret_fingerprint(password),
    }
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    _json_write(path, payload)
    return _project_payload(project_name)


def test_connection(name: str) -> dict[str, Any]:
    _profile_path, profile, _paths = _profile_and_paths(name)
    transport = _transport(profile)
    cwd = transport.test_connection()
    root_ok = transport.directory_exists(profile.remote_path)
    if not root_ok:
        raise RuntimeError(f"Kết nối FTP được nhưng không truy cập được {profile.remote_path}")
    return {"ok": True, "cwd": cwd, "remotePath": profile.remote_path, "tls": profile.use_tls}


@contextmanager
def _confirm_answers(answers: list[bool]):
    """Answer typer.confirm inside this GUI process without stdin interaction."""
    original = typer.confirm
    queue = list(answers)

    def fake_confirm(*_args, default: bool = False, **_kwargs) -> bool:
        if queue:
            return bool(queue.pop(0))
        return bool(default)

    typer.confirm = fake_confirm
    try:
        yield
    finally:
        typer.confirm = original


_PROGRESS_SIDEBAND_PHASES = {
    "ftp_reconnect",
    "ftp_path_fallback",
    "permission_recovery",
    "db_import_retry",
}
_REBUILD_PROGRESS_FIXED = {
    "verify_original": 1,
    "verify_clean": 3,
    "download_core": 5,
    "extract_core": 8,
    "destructive_boundary": 10,
    "wipe_inventory": 11,
    "wipe_inventory_complete": 12,
    "upload_wp_config": 59,
    "upload_htaccess": 60,
    "db_import_prepare": 83,
    "db_import_upload": 84,
    "db_import_execute": 90,
    "db_import_cleanup": 97,
    "complete": 100,
}
_REBUILD_PROGRESS_RANGES = {
    "wipe": (12, 32),
    "upload_core": (32, 58),
    "upload_uploads": (60, 82),
    "upload_plugins": (60, 82),
    "upload_themes": (60, 82),
    "upload_mu-plugins": (60, 82),
}


def _rebuild_progress_percent(phase: str, completed: int, total: int, current: int) -> int:
    if phase in _REBUILD_PROGRESS_FIXED:
        return _REBUILD_PROGRESS_FIXED[phase]
    bounds = _REBUILD_PROGRESS_RANGES.get(phase)
    if bounds is None:
        return current
    start, end = bounds
    ratio = min(1.0, completed / total) if total > 0 else 0.0
    return round(start + ((end - start) * ratio))


def _progress(job: GuiJob, event: dict[str, Any]) -> None:
    phase = str(event.get("phase") or "")
    current = str(
        event.get("current")
        or event.get("current_file")
        or event.get("current_dir")
        or event.get("remote_path")
        or event.get("file")
        or event.get("path")
        or event.get("url")
        or event.get("stage")
        or ""
    )

    if phase and phase not in _PROGRESS_SIDEBAND_PHASES and phase != job.progress_phase:
        job.progress_phase = phase
        job.completed_items = 0
        job.total_items = 0
        job.bytes_completed = 0
        job.bytes_total = 0
        job.bytes_per_second = 0.0
        job.failed_items = 0
        job.retry_attempt = 0
        job.retry_limit = 0
        job.progress_unit = "file"

    def number(*keys: str, fallback: int = 0) -> int:
        for key in keys:
            value = event.get(key)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    continue
        return max(0, int(fallback))

    completed = number(
        "items_completed",
        "files_completed",
        "items_discovered",
        "completed",
        fallback=job.completed_items,
    )
    total = number("items_total", "files_total", "total", fallback=job.total_items)
    bytes_completed = number("bytes_downloaded", "bytes_completed", fallback=job.bytes_completed)
    bytes_total = number("bytes_total", fallback=job.bytes_total)
    failed = number("files_failed", fallback=job.failed_items)
    attempt = number("attempt", fallback=0)
    max_attempts = number("max_attempts", fallback=0)
    plugin_total = number("plugin_total", fallback=0)
    if job.stage == "plugin" and plugin_total:
        plugin_completed = min(plugin_total, number("plugin_completed", fallback=0))
        completed = plugin_completed
        total = plugin_total
        file_total = number("files_total", fallback=0)
        file_completed = min(file_total, number("files_completed", fallback=0))
        within_plugin = (file_completed / file_total) if file_total else 0.0
        if phase == "plugin_lookup":
            percent = round(10 * (plugin_completed / plugin_total))
        else:
            ratio = min(1.0, (plugin_completed + within_plugin) / plugin_total)
            percent = round(10 + (85 * ratio))
    elif job.stage == "plugin" and phase == "upload_mu_plugin":
        ratio = min(1.0, completed / total) if total > 0 else 0.0
        percent = round(96 + (3 * ratio))
    elif job.stage == "rebuild" and phase == "db_import_execute" and bytes_total:
        ratio = min(1.0, bytes_completed / bytes_total)
        percent = round(84 + (12 * ratio))
    elif job.stage == "rebuild" and phase:
        percent = _rebuild_progress_percent(phase, completed, total, job.percent)
    elif total:
        percent = int((completed / total) * 100)
    elif bytes_total:
        percent = int((bytes_completed / bytes_total) * 100)
    else:
        percent = job.percent
    labels = {
        "discover": "Đang kiểm kê file",
        "discover_start": "Bắt đầu kiểm kê file",
        "discover_dir": "Đang đọc thư mục trên hosting",
        "discover_complete": "Đã kiểm kê xong file",
        "transfer": "Đang tải backup",
        "retry": "Kết nối tạm lỗi, đang thử lại",
        "file_failed": "Một file chưa tải được",
        "verify": "Đang xác minh SHA-256",
        "stage": "Đang chuyển bước backup",
        "config_file": "Đang backup file cấu hình",
        "upload_bridge": "Đang chuẩn bị backup database",
        "request_dump": "Đang xuất database",
        "download": "Đang tải database",
        "remove_bridge": "Đang dọn file tạm",
        "verify_original": "Đang xác minh backup gốc",
        "verify_clean": "Đang xác minh dữ liệu sạch",
        "download_core": "Đang tải WordPress sạch",
        "extract_core": "Đang kiểm tra WordPress",
        "destructive_boundary": "Bắt đầu rebuild website",
        "wipe_inventory": "Đang kiểm kê code cũ trước khi xóa",
        "wipe_inventory_complete": "Đã kiểm kê xong, chuẩn bị xóa code cũ",
        "wipe": "Đang xóa code cũ",
        "ftp_reconnect": "FTP tạm ngắt, đang kết nối lại",
        "ftp_path_fallback": "FTP đang đổi cách gửi lệnh xóa",
        "permission_recovery": "Đang xử lý quyền file trên hosting",
        "db_import_prepare": "Đang chuẩn hóa SQL cho MySQL",
        "db_import_upload": "Đang chuẩn bị import database",
        "db_import_execute": "Đang import database sạch",
        "db_import_retry": "Hosting ngắt request, đang tiếp tục từ checkpoint",
        "db_import_cleanup": "Đang dọn file import tạm",
        "plugin_lookup": "Đang xác minh plugin đã chọn",
        "plugin_download": "Đang tải plugin sạch",
        "plugin_upload_start": "Đang chuẩn bị upload plugin",
        "upload_wporg_plugin": "Đang upload plugin sạch",
        "plugin_installed": "Đã cài plugin sạch",
        "plugin_failed": "Một plugin cài chưa thành công",
        "upload_mu_plugin": "Đang upload MU-plugin sạch",
        "inventory": "Đang kiểm kê website hiện tại",
        "inventory_start": "Bắt đầu kiểm kê website",
        "verify_remote_root": "Đang xác minh đúng WordPress root",
        "complete": "Đã hoàn tất tác vụ hiện tại",
    }
    if phase.startswith("upload_") and phase not in labels:
        labels[phase] = "Đang upload dữ liệu sạch"
    message = labels.get(phase, phase.replace("_", " ") if phase else job.message)
    if attempt:
        message += f" · lần {attempt}/{max_attempts or '?'}"
    job.completed_items = completed
    job.total_items = total
    job.bytes_completed = bytes_completed
    job.bytes_total = bytes_total
    try:
        job.bytes_per_second = max(0.0, float(event.get("bytes_per_second") or job.bytes_per_second))
    except (TypeError, ValueError):
        pass
    job.failed_items = failed
    job.retry_attempt = attempt
    job.retry_limit = max_attempts
    job.progress_unit = str(event.get("unit") or job.progress_unit or "file")
    job.set(message=message, percent=percent, current=current)


def _run_backup_files(profile, transport, paths, job: GuiJob) -> None:
    report = backup_wordpress_ftp(
        transport,
        profile.remote_path,
        paths["backup"],
        resume=True,
        progress=lambda event: _progress(job, event),
    )
    if not report.verified:
        raise RuntimeError("Backup file chưa vượt qua kiểm tra toàn vẹn.")
    job.log(f"Backup file hoàn tất; exclusions={len(report.exclusions)}")


def _run_backup_db(profile, transport, paths, job: GuiJob) -> None:
    target = paths["backup"] / "database" / "original.sql"
    result = export_database_via_php_bridge(
        profile,
        transport,
        target,
        progress=lambda event: _progress(job, event),
    )
    manifest = write_manifest(paths["backup"])
    ok, problems = verify_manifest(paths["backup"], manifest)
    if not ok:
        raise RuntimeError("Backup database xong nhưng manifest không hợp lệ: " + "; ".join(problems))
    job.log(f"Database backup: {result.sql_path} | SHA-256 {result.sha256}")


def _run_verify_scan(profile, paths, job: GuiJob) -> None:
    backup_root = paths["backup"]
    ok, problems = verify_manifest(backup_root, backup_root / "manifest.json")
    if not ok:
        raise RuntimeError("Backup không còn nguyên vẹn: " + "; ".join(problems))
    job.set(message="Đang quét database và uploads", percent=40)
    sql_path = backup_root / "database" / "original.sql"
    uploads_path = backup_root / "uploads"
    db_findings = scan_sql(sql_path) if sql_path.is_file() else []
    upload_findings = scan_uploads(uploads_path) if uploads_path.is_dir() else []
    findings = [*db_findings, *upload_findings]
    blocking = [item for item in findings if getattr(item, "score", 0) >= 60]
    wizard._write_json(
        paths["scan"],
        {
            "host": profile.host,
            "database_findings": len(db_findings),
            "uploads_findings": len(upload_findings),
            "high_or_critical": len(blocking),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    job.log(f"Scan: {len(findings)} cảnh báo, HIGH/CRITICAL={len(blocking)}")


def _run_clean(profile, paths, job: GuiJob) -> None:
    job.set(message="Đang tạo clean staging", percent=30)
    report = build_clean_restore(paths["backup"], ftp_password=profile.password or "", host=profile.host)
    if not report.clean_verified:
        raise RuntimeError("Clean staging chưa vượt qua verify.")
    job.log(f"Clean uploads: giữ {report.uploads_copied}, loại {report.uploads_dropped}")


def _run_preflight(profile, transport, paths, job: GuiJob) -> None:
    job.set(message="Đang kiểm tra điều kiện rebuild", percent=40)
    report = run_rebuild_preflight(
        host=profile.host,
        transport=transport,
        remote_root=profile.remote_path,
        backup_root=paths["backup"],
        report_path=paths["preflight"],
        fast=True,
    )
    if not report.ready_for_destructive_rebuild:
        raise RuntimeError("Preflight chưa cho phép rebuild.")
    job.log("Preflight PASS")


def _rebuild_partial(paths: dict[str, Path]) -> bool:
    execution = _json_read(paths["execute"])
    return bool(
        not execution.get("database_imported")
        and (
            int(execution.get("core_uploaded") or 0) > 0
            or execution.get("wp_config_uploaded")
            or int(execution.get("wiped_files") or 0) > 0
        )
    )


def _gate(job: GuiJob, stage: str, title: str, message: str, decision: dict[str, Any]) -> None:
    job.set(
        status="needs-action",
        stage=stage,
        title=title,
        message=message,
        percent=0,
        current="",
        decision=decision,
    )


def _theme_gate(profile, paths, job: GuiJob) -> bool:
    active, _child_root = plan_theme_stage(paths["backup"])
    if active is None:
        _gate(
            job,
            "theme",
            "Không xác định được theme",
            "Hãy cài theme sạch thủ công rồi xác nhận để tiếp tục.",
            {"type": "manual-theme", "theme": "Không xác định"},
        )
        return True
    if not active.is_flatsome:
        _gate(
            job,
            "theme",
            "Theme cần cài thủ công",
            f"Website dùng theme {active.stylesheet or active.template}. Hãy cài bản sạch rồi xác nhận.",
            {"type": "manual-theme", "theme": active.stylesheet or active.template},
        )
        return True
    repair = operator.existing_child_theme_repair(paths["backup"], active.stylesheet) if active.has_child else None
    _gate(
        job,
        "theme",
        "Cài theme an toàn",
        "Flatsome sẽ lấy từ package tin cậy. Theme con chỉ upload sau khi scan PASS.",
        {
            "type": "theme",
            "template": active.template,
            "stylesheet": active.stylesheet,
            "hasChild": active.has_child,
            "childSlug": active.stylesheet if active.has_child else "",
            "repairPath": str(repair or ""),
        },
    )
    return True


def _save_quick_final(profile, paths, confirmed: bool, home: dict, admin: dict) -> None:
    if confirmed:
        warnings = [
            "Đã bỏ qua deep live filesystem/checksum scan theo workflow GUI nhanh; kết quả dựa trên HTTP smoke test và xác nhận thủ công của nhân sự."
        ]
        if not home.get("ok"):
            warnings.append("HTTP trang chủ không PASS nhưng người vận hành đã xác nhận thủ công.")
        if not admin.get("ok"):
            warnings.append("HTTP wp-admin không PASS nhưng người vận hành đã xác nhận thủ công.")
        payload = {
            "status": "PASS WITH WARNINGS",
            "mode": "gui-quick-check",
            "deep_scan_run": False,
            "operator_confirmed": True,
            "home": home,
            "admin": admin,
            "warnings": warnings,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        payload = {
            "status": "MANUAL REVIEW REQUIRED",
            "mode": "gui-quick-check",
            "deep_scan_run": False,
            "operator_confirmed": False,
            "home": home,
            "admin": admin,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    wizard._write_json(paths["final"], payload)


def _run_pipeline(name: str, options: dict[str, Any], job: GuiJob) -> None:
    global ACTIVE_PROJECT
    try:
        _profile_path, profile, paths = _profile_and_paths(name)
        transport = _transport(profile)
        job.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        job.set(status="running", title="Đang chuẩn bị", message="Đang kiểm tra kết nối hosting", decision=None)
        cwd = transport.test_connection()
        if not transport.directory_exists(profile.remote_path):
            raise RuntimeError(f"Không truy cập được WordPress root: {profile.remote_path}")
        job.log(f"FTP OK: {cwd}")

        for _ in range(20):
            status = operator._infer_status(paths)
            stage = operator._next_stage(status)
            job.set(stage=stage, title=operator.wizard.STAGE_LABELS.get(stage, stage), percent=0, current="")

            if stage == "done":
                job.set(status="success", title="Hoàn tất", message="Dự án đã hoàn tất.", percent=100, decision=None)
                return

            if stage == "backup-files":
                job.set(message="Đang backup toàn bộ file website")
                _run_backup_files(profile, transport, paths, job)
            elif stage == "backup-db":
                job.set(message="Đang backup database")
                _run_backup_db(profile, transport, paths, job)
            elif stage == "verify-scan":
                _run_verify_scan(profile, paths, job)
            elif stage == "clean":
                _run_clean(profile, paths, job)
            elif stage == "preflight":
                _run_preflight(profile, transport, paths, job)
            elif stage == "rebuild":
                if _rebuild_partial(paths):
                    if not options.get("resumeDb"):
                        _gate(
                            job,
                            stage,
                            "Website đã rebuild một phần",
                            "Core đã thay đổi nhưng database chưa hoàn tất. Tiếp tục bằng chế độ DB-only, không wipe lại website.",
                            {"type": "resume-db"},
                        )
                        return
                    job.set(message="Đang tiếp tục import database, không wipe lại", percent=10)
                    resume_database_import(
                        profile=profile,
                        transport=transport,
                        backup_root=paths["backup"],
                        execution_report_path=paths["execute"],
                        progress=lambda event: _progress(job, event),
                    )
                else:
                    if str(options.get("confirmRebuild") or "") != profile.host:
                        _gate(
                            job,
                            stage,
                            "Xác nhận rebuild WordPress",
                            f"Code cũ trong {profile.remote_path} sẽ bị xóa sau khi backup/clean/preflight được xác minh lại.",
                            {"type": "rebuild", "confirmText": profile.host, "remotePath": profile.remote_path},
                        )
                        return
                    job.set(message="Đang rebuild WordPress + database", percent=5)
                    report = execute_rebuild(
                        profile=profile,
                        transport=transport,
                        backup_root=paths["backup"],
                        preflight_path=paths["preflight"],
                        report_path=paths["execute"],
                        restore_backup_code=False,
                        progress=lambda event: _progress(job, event),
                    )
                    if not report.database_imported:
                        raise RuntimeError("Rebuild xong nhưng database chưa import thành công.")
            elif stage == "theme":
                if options.get("manualThemeAck"):
                    wizard._save_operator_state(paths, profile, manual_theme_ack=True)
                elif "themeInstall" not in options:
                    _theme_gate(profile, paths, job)
                    return
                else:
                    active, _ = plan_theme_stage(paths["backup"])
                    answers = [bool(options.get("themeInstall", True))]
                    if active and active.has_child:
                        answers.append(bool(options.get("childInstall", True)))
                    with _confirm_answers(answers):
                        result = rebuild_entry._run_theme_stage(
                            profile=profile,
                            transport=transport,
                            backup_root=paths["backup"],
                            report_path=paths["execute"],
                        )
                    if result.child_repair_workspace and not result.child_installed:
                        _gate(
                            job,
                            stage,
                            "Theme con cần kỹ thuật sửa",
                            "Tool đã chặn upload theme con. Sửa working-copy rồi bấm Quét lại.",
                            {
                                "type": "theme-repair",
                                "path": result.child_repair_workspace,
                                "childSlug": result.child_theme_slug,
                            },
                        )
                        return
                    if result.unsupported_theme or result.mode == "detection-unavailable":
                        _gate(
                            job,
                            stage,
                            "Theme cần cài thủ công",
                            "Cài theme sạch thủ công rồi xác nhận để tiếp tục.",
                            {"type": "manual-theme", "theme": result.unsupported_theme or "Không xác định"},
                        )
                        return
            elif stage == "plugin":
                if not status.get("plugin_done"):
                    choices = plugin_workflow.plugin_install_choices(paths["backup"])
                    if choices and not options.get("pluginSelectionConfirmed"):
                        _gate(
                            job,
                            stage,
                            "Chọn plugin muốn cài",
                            f"Phát hiện {len(choices)} plugin trong backup. Chỉ plugin được chọn mới được lấy bản sạch để cài.",
                            {"type": "plugins", "plugins": choices},
                        )
                        return
                    raw_selection = options.get("pluginSelection", [])
                    if not isinstance(raw_selection, list):
                        raise ValueError("Danh sách plugin được chọn không hợp lệ.")
                    selected_slugs = [str(slug) for slug in raw_selection]
                    job.set(message="Đang xác minh và cài plugin đã chọn", percent=0)
                else:
                    selected_slugs = None
                    job.set(message="Đang quét và khôi phục MU-plugin", percent=96)
                operator._stage_plugin(
                    profile,
                    transport,
                    paths,
                    selected_slugs=selected_slugs,
                    progress=lambda event: _progress(job, event),
                )
                status_after = operator._infer_status(paths)
                if not status_after.get("plugin_done") or not status_after.get("mu_plugin_done"):
                    raise RuntimeError("Plugin/MU-plugin chưa hoàn tất; hãy thử lại stage này.")
            elif stage == "manual-plugins":
                if not options.get("manualPluginsAck"):
                    _gate(
                        job,
                        stage,
                        "Plugin cần cài thủ công",
                        f"Có {status.get('plugin_manual', 0)} plugin không có trên WordPress.org. Cài bản sạch từ nhà cung cấp rồi xác nhận.",
                        {"type": "manual-plugins", "count": int(status.get("plugin_manual") or 0)},
                    )
                    return
                wizard._save_operator_state(
                    paths,
                    profile,
                    manual_plugins_ack=True,
                    manual_plugins_note="uploaded" if options.get("manualPluginsUploaded", True) else "accepted-for-later",
                )
            elif stage == "final":
                home = operator._http_probe(profile.web_base_url.rstrip("/") + "/")
                admin = operator._http_probe(profile.web_base_url.rstrip("/") + "/wp-admin/")
                final_choice = str(options.get("finalChoice") or "")
                if final_choice == "deep":
                    result = operator._original_stage_final(profile, transport, paths)
                    if result == "BLOCKED":
                        job.set(status="paused", title="Kiểm tra sâu chưa PASS", message="Còn mục cần kỹ thuật xử lý.")
                        return
                elif final_choice == "complete":
                    _save_quick_final(profile, paths, True, home, admin)
                elif final_choice == "problem":
                    _save_quick_final(profile, paths, False, home, admin)
                    job.set(status="paused", title="Chờ kỹ thuật xử lý", message="Đã ghi nhận website còn lỗi cần sửa.", decision=None)
                    return
                else:
                    _gate(
                        job,
                        stage,
                        "Kiểm tra nhanh website",
                        "Mở frontend và wp-admin, kiểm tra warning PHP/giao diện/chức năng chính rồi xác nhận.",
                        {"type": "final", "home": home, "admin": admin, "url": profile.web_base_url, "adminUrl": profile.web_base_url.rstrip("/") + "/wp-admin/"},
                    )
                    return
            else:
                raise RuntimeError(f"Stage GUI chưa hỗ trợ: {stage}")

            wizard._save_operator_state(paths, profile, last_completed_stage=stage)
            job.set(percent=100, message=f"Đã hoàn tất: {operator.wizard.STAGE_LABELS.get(stage, stage)}")
            job.log(f"PASS: {stage}")
            options = {}
            time.sleep(0.08)

        raise RuntimeError("Workflow vượt quá số vòng stage an toàn.")
    except wizard.TamDungQuyTrinh as exc:
        job.set(status="paused", title="Tạm dừng", message=str(exc), error="", decision=None)
    except Exception as exc:
        job.fail(exc, technical=traceback.format_exc(limit=8))
    finally:
        with JOBS_LOCK:
            ACTIVE_PROJECT = None


def start_job(name: str, options: dict[str, Any]) -> GuiJob:
    global ACTIVE_PROJECT
    _safe_project_path(name)
    with JOBS_LOCK:
        existing = JOBS.get(name)
        if existing and existing.status == "running":
            return existing
        if ACTIVE_PROJECT and ACTIVE_PROJECT != name:
            raise RuntimeError(f"Đang xử lý dự án {ACTIVE_PROJECT}. Hãy chờ stage hiện tại hoàn tất.")
        ACTIVE_PROJECT = name
        job = GuiJob(project=name, status="running", title="Đang bắt đầu", message="Chuẩn bị workflow")
        job.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        job.touch()
        JOBS[name] = job
    thread = threading.Thread(target=_run_pipeline, args=(name, options, job), daemon=True, name=f"wpclean-gui-{name}")
    thread.start()
    return job


def delete_project_local(name: str, confirmation: str) -> None:
    from . import project_delete_command as delete_module

    if confirmation != name:
        raise ValueError("Tên xác nhận không khớp.")
    payload = _project_payload(name)
    if not payload.get("completed"):
        raise RuntimeError("Chỉ được xóa project local sau khi dự án đã hoàn tất.")
    profile_path, profile, _paths = _profile_and_paths(name)
    for path, root in (
        (BACKUPS_DIR / profile.host, BACKUPS_DIR),
        (REPORTS_DIR / profile.host, REPORTS_DIR),
        (REPAIRS_DIR / profile.host, REPAIRS_DIR),
    ):
        delete_module._delete_local_path(path, root)
    delete_module._delete_local_path(profile_path, SITES_DIR)
    JOBS.pop(name, None)


def open_repair(name: str) -> str:
    payload = _project_payload(name)
    path = Path(str(payload.get("themeRepair") or ""))
    if not path.is_dir():
        raise FileNotFoundError("Dự án chưa có thư mục theme repair.")
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        raise RuntimeError("Mở Explorer tự động hiện chỉ hỗ trợ Windows.")
    return str(path)


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = min(int(handler.headers.get("Content-Length") or 0), 1024 * 1024)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body phải là object.")
    return payload


def _send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)


def _send_html(handler: BaseHTTPRequestHandler, html: str) -> None:
    data = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)


class GuiHandler(BaseHTTPRequestHandler):
    server_version = "WPCleanGUI/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-WPClean-Token") or "", TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                from .gui_ui import render_app
                _send_html(self, render_app(TOKEN))
                return
            if path == "/api/projects":
                _send_json(self, {"projects": list_projects(), "activeProject": ACTIVE_PROJECT})
                return
            match = re.fullmatch(r"/api/projects/([^/]+)", path)
            if match:
                _send_json(self, _project_payload(unquote(match.group(1))))
                return
            match = re.fullmatch(r"/api/jobs/([^/]+)", path)
            if match:
                name = unquote(match.group(1))
                job = JOBS.get(name)
                _send_json(self, job.to_dict() if job else {"project": name, "status": "idle"})
                return
            _send_json(self, {"error": "Không tìm thấy endpoint."}, 404)
        except Exception as exc:
            _send_json(self, error_payload(exc), 400)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authorized():
            _send_json(
                self,
                {
                    "error": "Phiên GUI đã hết hiệu lực. Hãy tải lại trang.",
                    "errorCode": "GUI-AUTH-001",
                    "errorTitle": "Phiên GUI không hợp lệ",
                    "recovery": "Refresh trang để nhận phiên local mới.",
                    "retryable": True,
                },
                403,
            )
            return
        try:
            body = _read_body(self)
            if path == "/api/projects":
                _send_json(self, create_project(body), 201)
                return
            match = re.fullmatch(r"/api/projects/([^/]+)/test", path)
            if match:
                _send_json(self, test_connection(unquote(match.group(1))))
                return
            match = re.fullmatch(r"/api/projects/([^/]+)/advance", path)
            if match:
                name = unquote(match.group(1))
                _send_json(self, start_job(name, body).to_dict(), 202)
                return
            match = re.fullmatch(r"/api/projects/([^/]+)/open-repair", path)
            if match:
                _send_json(self, {"ok": True, "path": open_repair(unquote(match.group(1)))})
                return
            match = re.fullmatch(r"/api/projects/([^/]+)/delete", path)
            if match:
                name = unquote(match.group(1))
                result = delete_project_local(name, str(body.get("confirmation") or ""))
                payload = {"ok": True}
                if isinstance(result, dict):
                    payload.update(result)
                _send_json(self, payload, 202 if payload.get("status") == "running" else 200)
                return
            _send_json(self, {"error": "Không tìm thấy endpoint."}, 404)
        except Exception as exc:
            _send_json(self, error_payload(exc), 400)


def _bind_server(host: str = "127.0.0.1", preferred: int = 8765) -> tuple[ThreadingHTTPServer, int]:
    last: Exception | None = None
    for port in range(preferred, preferred + 20):
        try:
            return ThreadingHTTPServer((host, port), GuiHandler), port
        except OSError as exc:
            last = exc
    raise RuntimeError(f"Không tìm được cổng local trống: {last}")


def main() -> None:
    server, port = _bind_server()
    url = f"http://127.0.0.1:{port}/"
    print("\nWP CLEAN REBUILD — GIAO DIỆN LOCAL")
    print(f"Đang chạy tại: {url}")
    print("Chỉ bind 127.0.0.1; máy khác trong mạng không truy cập được.")
    print("Đóng cửa sổ này để dừng GUI.\n")
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
