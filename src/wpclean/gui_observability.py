from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import re
from typing import Any


_ERROR_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+-\d{3})\b")


@dataclass(frozen=True)
class OperatorError:
    """A stable, operator-facing error contract shared by the API and GUI."""

    code: str
    title: str
    message: str
    recovery: str
    retryable: bool
    technical: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def operator_line(self) -> str:
        return f"[{self.code}] {self.title} · {self.message}"


class OperationError(RuntimeError):
    """Exception that already carries a safe explanation for an operator."""

    def __init__(self, info: OperatorError) -> None:
        super().__init__(info.operator_line())
        self.info = info


def _error(
    code: str,
    title: str,
    message: str,
    recovery: str,
    technical: str,
    *,
    retryable: bool = True,
) -> OperatorError:
    return OperatorError(
        code=code,
        title=title,
        message=message,
        recovery=recovery,
        retryable=retryable,
        technical=technical,
    )


def classify_exception(exc: BaseException, *, stage: str = "") -> OperatorError:
    """Translate technical failures into stable Vietnamese operator guidance."""

    if isinstance(exc, OperationError):
        return exc.info

    technical = f"{type(exc).__name__}: {exc}"
    text = technical.lower()
    stage_key = str(stage or "").strip().lower()

    # Preserve a code that has already crossed a compatibility boundary.
    existing = _ERROR_CODE_RE.search(technical)
    if existing:
        code = existing.group(1)
        return _error(
            code,
            "Bước xử lý chưa hoàn tất",
            str(exc),
            "Đọc hướng dẫn trong log, xử lý nguyên nhân rồi thử lại đúng bước hiện tại.",
            technical,
        )

    if "530" in text or any(
        marker in text
        for marker in ("login authentication failed", "authentication failed", "not logged in")
    ):
        return _error(
            "FTP-AUTH-001",
            "Không đăng nhập được FTP",
            "Hosting đã phản hồi nhưng từ chối tài khoản hoặc mật khẩu FTP.",
            "Mở Sửa FTP, nhập lại tài khoản/mật khẩu rồi bấm Kiểm tra kết nối.",
            technical,
        )

    if "getaddrinfo" in text or any(
        marker in text for marker in ("name or service not known", "nodename nor servname", "no such host")
    ):
        return _error(
            "FTP-DNS-001",
            "Không tìm thấy FTP host",
            "Máy không phân giải được tên FTP host.",
            "Kiểm tra chính tả FTP host và kết nối Internet rồi thử lại.",
            technical,
        )

    if "10061" in text or "connection refused" in text or "actively refused" in text:
        return _error(
            "FTP-CONNECT-001",
            "FTP từ chối kết nối",
            "Máy chủ có phản hồi nhưng port hoặc dịch vụ FTP không chấp nhận kết nối.",
            "Kiểm tra giao thức FTP/FTPS, port và trạng thái dịch vụ trên hosting.",
            technical,
        )

    if "10060" in text or "timed out" in text or "timeout" in text:
        code = "FTP-TIMEOUT-001" if stage_key.startswith("ftp") or "ftp" in text else "NETWORK-TIMEOUT-001"
        return _error(
            code,
            "Kết nối không phản hồi",
            "Hosting hoặc dịch vụ bên ngoài không phản hồi trong thời gian cho phép.",
            "Kiểm tra mạng/hosting, chờ một lúc rồi thử lại đúng bước hiện tại.",
            technical,
        )

    if "550" in text or "permission denied" in text or "access is denied" in text:
        return _error(
            "FTP-PERM-001",
            "Hosting từ chối quyền thao tác",
            "Tài khoản FTP không có quyền đọc, ghi hoặc xóa một đường dẫn cần thiết.",
            "Kiểm tra owner/quyền file trên hosting rồi thử lại; không chạy wipe thủ công.",
            technical,
        )

    if any(marker in text for marker in ("ssl", "tls", "certificate")):
        return _error(
            "FTP-TLS-001",
            "Không thiết lập được FTPS",
            "Bắt tay TLS hoặc chứng chỉ của máy chủ FTP không hợp lệ.",
            "Kiểm tra giao thức/port FTPS với nhà cung cấp hosting.",
            technical,
        )

    if isinstance(exc, FileExistsError):
        return _error(
            "PROJECT-EXISTS-001",
            "Tên dự án đã tồn tại",
            "Đã có một dự án local sử dụng tên này.",
            "Mở dự án hiện có hoặc chọn một tên dự án khác.",
            technical,
            retryable=False,
        )

    if isinstance(exc, FileNotFoundError):
        return _error(
            "FILE-MISSING-001",
            "Thiếu file cần thiết",
            "Một file đầu vào hoặc file backup đã biến mất trong khi xử lý.",
            "Kiểm tra antivirus/cách ly file và tính toàn vẹn backup trước khi thử lại.",
            technical,
        )

    if "manifest" in text or "sha-256" in text or "toàn vẹn" in text:
        return _error(
            "BACKUP-INTEGRITY-001",
            "Backup không còn nguyên vẹn",
            "Kiểm tra SHA-256 hoặc manifest của backup không đạt yêu cầu.",
            "Không rebuild. Kiểm tra report và tạo/xác minh lại backup trước.",
            technical,
            retryable=False,
        )

    if "preflight" in text:
        return _error(
            "REBUILD-PREFLIGHT-001",
            "Chưa đủ điều kiện rebuild",
            "Preflight đã chặn bước phá hủy để bảo vệ website.",
            "Mở report preflight, xử lý mục bị chặn rồi chạy lại preflight.",
            technical,
            retryable=False,
        )

    if "database" in text and any(marker in text for marker in ("import", "mysql", "bridge")):
        return _error(
            "DB-IMPORT-001",
            "Import database chưa hoàn tất",
            "Database sạch chưa được import thành công.",
            "Dùng chức năng tiếp tục DB-only; không wipe website lần nữa.",
            technical,
        )

    if isinstance(exc, (ValueError, KeyError, json.JSONDecodeError)):
        return _error(
            "INPUT-VALIDATION-001",
            "Dữ liệu nhập chưa hợp lệ",
            str(exc).strip("'\"") or "Một giá trị bắt buộc chưa đúng định dạng.",
            "Kiểm tra lại trường được báo lỗi rồi gửi lại.",
            technical,
            retryable=False,
        )

    stage_prefix = {
        "ftp-test": "FTP",
        "backup-files": "BACKUP",
        "backup-db": "DB-EXPORT",
        "verify-scan": "SCAN",
        "clean": "CLEAN",
        "rebuild": "REBUILD",
        "theme": "THEME",
        "plugin": "PLUGIN",
        "manual-plugins": "PLUGIN",
        "final": "VERIFY",
    }.get(stage_key, "WPCLEAN")
    return _error(
        f"{stage_prefix}-UNEXPECTED-001",
        "Bước xử lý dừng do lỗi",
        str(exc) or type(exc).__name__,
        "Mở Chi tiết kỹ thuật, xử lý nguyên nhân rồi thử lại đúng bước hiện tại.",
        technical,
    )


def error_payload(exc: BaseException, *, stage: str = "") -> dict[str, Any]:
    info = classify_exception(exc, stage=stage)
    return {
        "error": info.message,
        "errorCode": info.code,
        "errorTitle": info.title,
        "recovery": info.recovery,
        "retryable": info.retryable,
        "technical": info.technical,
    }


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def job_timing(
    *,
    started_at: str,
    updated_at: str,
    status: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return live timing/health without mutating the job as the UI polls it."""

    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    started = _parse_timestamp(started_at)
    updated = _parse_timestamp(updated_at)
    elapsed = max(0, int((current - started).total_seconds())) if started else 0
    idle = max(0, int((current - updated).total_seconds())) if updated else 0

    health = "idle"
    label = "Sẵn sàng"
    if status == "running":
        if idle < 15:
            health, label = "active", "Đang hoạt động"
        elif idle < 60:
            health, label = "waiting", "Đang chờ phản hồi"
        elif idle < 180:
            health, label = "slow", "Phản hồi chậm"
        else:
            health, label = "stalled", "Có dấu hiệu bị treo"
    elif status == "needs-action":
        health, label = "attention", "Chờ xác nhận"
    elif status == "paused":
        health, label = "attention", "Tạm dừng"
    elif status == "error":
        health, label = "error", "Đã dừng do lỗi"
    elif status == "success":
        health, label = "success", "Hoàn tất"

    return {
        "elapsedSeconds": elapsed,
        "idleSeconds": idle,
        "health": health,
        "healthLabel": label,
        "isStalled": health == "stalled",
        "serverTime": current.isoformat(timespec="seconds"),
    }


__all__ = [
    "OperationError",
    "OperatorError",
    "classify_exception",
    "error_payload",
    "job_timing",
]
