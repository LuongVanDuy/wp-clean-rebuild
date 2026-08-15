from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Iterable


_LOCK = threading.RLock()
ACTIVITY_FILE = "activity-log.jsonl"
NOTES_FILE = "project-notes.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _redact(text: str, secrets: Iterable[str] = ()) -> str:
    clean = str(text)
    for secret in secrets:
        value = str(secret or "")
        if len(value) >= 3:
            clean = clean.replace(value, "***")
    return clean


def append_activity(
    report_dir: Path,
    *,
    project: str,
    message: str,
    stage: str = "",
    level: str = "info",
    session_id: str = "",
    secrets: Iterable[str] = (),
    code: str = "",
    title: str = "",
    recovery: str = "",
    technical: str = "",
) -> dict[str, Any] | None:
    text = _redact(str(message).strip(), secrets)
    if not text:
        return None
    item = {
        "timestamp": _now(),
        "project": str(project),
        "stage": str(stage or ""),
        "level": str(level or "info"),
        "session": str(session_id or ""),
        "message": text,
    }
    if code:
        item["code"] = str(code)
    if title:
        item["title"] = _redact(title, secrets)
    if recovery:
        item["recovery"] = _redact(recovery, secrets)
    if technical:
        item["technical"] = _redact(technical, secrets)
    path = Path(report_dir) / ACTIVITY_FILE
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
    return item


def read_activity(report_dir: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    path = Path(report_dir) / ACTIVITY_FILE
    if not path.is_file():
        return []
    cap = max(1, min(int(limit), 5000))
    rows: list[dict[str, Any]] = []
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    for line in lines[-cap:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("message"):
            rows.append(item)
    return rows


def _notes_path(report_dir: Path) -> Path:
    return Path(report_dir) / NOTES_FILE


def _empty_notes() -> dict[str, Any]:
    return {"version": 1, "updated_at": "", "todos": []}


def load_notes(report_dir: Path) -> dict[str, Any]:
    path = _notes_path(report_dir)
    if not path.is_file():
        return _empty_notes()
    with _LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_notes()
    if not isinstance(payload, dict):
        return _empty_notes()
    todos = payload.get("todos")
    if not isinstance(todos, list):
        payload["todos"] = []
    payload.setdefault("version", 1)
    payload.setdefault("updated_at", "")
    return payload


def _write_notes(report_dir: Path, payload: dict[str, Any]) -> None:
    path = _notes_path(report_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = 1
    payload["updated_at"] = _now()
    temp = path.with_name(path.name + ".tmp")
    with _LOCK:
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)


def todo_id(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]


def upsert_todo(
    report_dir: Path,
    *,
    key: str,
    kind: str,
    title: str,
    detail: str = "",
    status: str = "pending",
    force_status: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wanted = "done" if status == "done" else "pending"
    notes = load_notes(report_dir)
    todos = notes["todos"]
    identifier = todo_id(key)
    now = _now()
    found: dict[str, Any] | None = None
    for item in todos:
        if isinstance(item, dict) and item.get("id") == identifier:
            found = item
            break
    if found is None:
        found = {
            "id": identifier,
            "key": key,
            "kind": kind,
            "title": title,
            "detail": detail,
            "status": wanted,
            "source": "auto",
            "created_at": now,
            "updated_at": now,
            "completed_at": now if wanted == "done" else "",
            "metadata": metadata or {},
        }
        todos.append(found)
    else:
        found["kind"] = kind
        found["title"] = title
        found["detail"] = detail
        found["metadata"] = metadata or found.get("metadata") or {}
        if force_status or found.get("status") not in {"pending", "done"}:
            found["status"] = wanted
            found["completed_at"] = now if wanted == "done" else ""
        found["updated_at"] = now
    _write_notes(report_dir, notes)
    return found


def set_todo_status(report_dir: Path, identifier: str, *, completed: bool) -> dict[str, Any]:
    notes = load_notes(report_dir)
    now = _now()
    for item in notes["todos"]:
        if isinstance(item, dict) and str(item.get("id")) == str(identifier):
            item["status"] = "done" if completed else "pending"
            item["status_source"] = "operator"
            item["updated_at"] = now
            item["completed_at"] = now if completed else ""
            _write_notes(report_dir, notes)
            return item
    raise KeyError(f"Không tìm thấy việc cần làm: {identifier}")


def _operator_uploaded_plugins(operator_state: dict[str, Any]) -> bool:
    return bool(
        operator_state.get("manual_plugins_ack")
        and str(operator_state.get("manual_plugins_note") or "") == "uploaded"
    )


def reconcile_automatic_todos(
    report_dir: Path,
    *,
    execution: dict[str, Any] | None = None,
    operator_state: dict[str, Any] | None = None,
    project_completed: bool = False,
    ftp_password_changed: bool = False,
) -> list[dict[str, Any]]:
    execution = execution if isinstance(execution, dict) else {}
    operator_state = operator_state if isinstance(operator_state, dict) else {}
    theme = execution.get("theme_stage") if isinstance(execution.get("theme_stage"), dict) else {}
    plugins = execution.get("plugin_stage") if isinstance(execution.get("plugin_stage"), dict) else {}

    if project_completed:
        upsert_todo(
            report_dir,
            key="security:rotate-hosting-wordpress-credentials",
            kind="security",
            title="Đổi lại mật khẩu hosting và WordPress",
            detail=(
                "Đã ghi nhận mật khẩu FTP trong cấu hình được thay đổi sau khi tạo dự án."
                if ftp_password_changed
                else "Mật khẩu FTP vẫn trùng với mật khẩu lúc tạo dự án. Hãy đổi mật khẩu tài khoản "
                "hosting/FTP và tài khoản quản trị WordPress sau khi hoàn tất xử lý."
            ),
            status="done" if ftp_password_changed else "pending",
            force_status=ftp_password_changed,
            metadata={"ftp_password_changed": ftp_password_changed},
        )

    unsupported = str(theme.get("unsupported_theme") or "").strip()
    detection_unavailable = str(theme.get("mode") or "") == "detection-unavailable"
    if unsupported or detection_unavailable:
        theme_name = unsupported or "Không xác định"
        upsert_todo(
            report_dir,
            key=f"manual-theme:{theme_name}",
            kind="theme",
            title=f"Cài theme {theme_name} từ nguồn sạch",
            detail="Theme này không được tool tự cài. Lấy package sạch từ khách hàng/nhà cung cấp, không restore code theme từ backup nhiễm.",
            status="done" if operator_state.get("manual_theme_ack") else "pending",
            force_status=bool(operator_state.get("manual_theme_ack")),
            metadata={"theme": theme_name},
        )

    child_slug = str(theme.get("child_theme_slug") or theme.get("child_slug") or "").strip()
    repair_path = str(theme.get("child_repair_workspace") or "").strip()
    child_installed = bool(theme.get("child_installed"))
    if repair_path:
        upsert_todo(
            report_dir,
            key=f"theme-repair:{child_slug or repair_path}",
            kind="theme-repair",
            title=f"Sửa và quét lại theme con {child_slug or 'không xác định'}",
            detail=f"Working-copy: {repair_path}",
            status="done" if child_installed else "pending",
            force_status=child_installed,
            metadata={"theme": child_slug, "path": repair_path},
        )

    uploaded = _operator_uploaded_plugins(operator_state)
    classifications = plugins.get("classifications") if isinstance(plugins.get("classifications"), list) else []
    for classification in classifications:
        if not isinstance(classification, dict):
            continue
        status = str(classification.get("status") or "")
        inventory = classification.get("inventory") if isinstance(classification.get("inventory"), dict) else {}
        slug = str(inventory.get("slug") or "").strip()
        name = str(inventory.get("name") or slug or "Plugin không xác định").strip()
        if status == "manual":
            upsert_todo(
                report_dir,
                key=f"manual-plugin:{slug or name}",
                kind="plugin",
                title=f"Cài plugin {name}",
                detail="Plugin không có trên WordPress.org. Cài bản sạch từ vendor/nguồn tin cậy; không dùng lại code plugin trong backup nhiễm.",
                status="done" if uploaded else "pending",
                force_status=uploaded,
                metadata={"slug": slug, "name": name},
            )
        elif status == "lookup-error":
            detail = str(classification.get("detail") or "Không xác minh được plugin trên WordPress.org.")
            upsert_todo(
                report_dir,
                key=f"plugin-lookup:{slug or name}",
                kind="plugin-check",
                title=f"Kiểm tra lại nguồn plugin {name}",
                detail=detail,
                status="pending",
                metadata={"slug": slug, "name": name},
            )

    notes = load_notes(report_dir)
    todos = [item for item in notes.get("todos", []) if isinstance(item, dict)]
    return sorted(
        todos,
        key=lambda item: (
            0 if item.get("status") == "pending" else 1,
            str(item.get("created_at") or ""),
            str(item.get("title") or "").lower(),
        ),
    )


__all__ = [
    "ACTIVITY_FILE",
    "NOTES_FILE",
    "append_activity",
    "read_activity",
    "load_notes",
    "todo_id",
    "upsert_todo",
    "set_todo_status",
    "reconcile_automatic_todos",
]
