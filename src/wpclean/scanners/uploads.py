from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from ..models import Finding, Signal
from ..risk import severity_for

EXECUTABLE_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".pdf", ".mp4", ".mov", ".mp3", ".wav", ".ico"}
TEXT_SUFFIXES = {".txt", ".html", ".htm", ".js", ".css", ".xml", ".json", ".htaccess", ".ini"}

STRICT_PHP_TOKEN = re.compile(br"<\?(?:php\b|=)", re.I)
SHORT_PHP_TOKEN = re.compile(br"<\?(?!xml\b)", re.I)
PHP_CODE_HINT = re.compile(
    br"(?:\$_(?:GET|POST|REQUEST|COOKIE|SERVER)\s*\[|\b(?:eval|assert|base64_decode|gzinflate|str_rot13|system|exec|shell_exec|passthru|include|include_once|require|require_once|echo|print|function)\b)",
    re.I,
)
PHP_ECHO_EXPR_HINT = re.compile(
    br"(?:\$_(?:GET|POST|REQUEST|COOKIE|SERVER)\s*\[|\$[A-Za-z_][A-Za-z0-9_]*|\b(?:eval|assert|base64_decode|gzinflate|str_rot13|system|exec|shell_exec|passthru)\s*\(|[A-Za-z_][A-Za-z0-9_\\]*\s*\()",
    re.I,
)
DOUBLE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|pdf)\.(?:php\d*|phtml|phar)$", re.I)
ARCHIVE_EXECUTABLE = re.compile(r"(?:^|/)[^/]+\.(?:php\d*|phtml|phar)$", re.I)

ARCHIVE_OBFUSCATION = re.compile(br"\b(?:gzinflate|gzdecode|base64_decode|str_rot13|hex2bin)\s*\(", re.I)
ARCHIVE_EXECUTION = re.compile(br"\b(?:eval|assert|system|exec|shell_exec|passthru)\s*\(", re.I)
ARCHIVE_USER_PERSISTENCE = re.compile(
    br"\b(?:username_exists|wp_create_user|wp_insert_user|wp_update_user|wp_set_password|set_role)\s*\(", re.I
)
ARCHIVE_CRON_PERSISTENCE = re.compile(br"\b(?:wp_schedule_event|wp_next_scheduled|wp_schedule_single_event)\s*\(", re.I)
ARCHIVE_FILE_MUTATION = re.compile(
    br"\b(?:file_put_contents|fwrite|chmod|rename|copy|unlink|opcache_invalidate)\s*\(", re.I
)
ARCHIVE_REMOTE_IO = re.compile(
    br"\b(?:wp_remote_get|wp_remote_post|curl_exec|curl_multi_exec|fsockopen|pfsockopen)\s*\(", re.I
)
ARCHIVE_RANDOM_FUNCTION = re.compile(br"\bfunction\s+[a-z0-9_]{12,}\s*\(", re.I)

MAX_ARCHIVE_PHP_FILES = 25
MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _magic_type(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp" and b"avif" in head[8:32]:
        return "avif"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.lstrip().startswith(b"<svg") or b"<svg" in head[:512].lower():
        return "svg"
    return "unknown"


def _safe_preview(data: bytes, limit: int = 220) -> str:
    text = data.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(1 for value in data if value in (9, 10, 13) or 32 <= value <= 126)
    return printable / len(data)


def _looks_like_php_source(window: bytes, token: bytes) -> bool:
    sample = window[:512]
    if len(sample) < 12 or _printable_ratio(sample) < 0.82:
        return False

    token_lower = token.lower()
    if token_lower.startswith(b"<?php"):
        return PHP_CODE_HINT.search(sample) is not None

    body = sample[len(token) :]
    return PHP_ECHO_EXPR_HINT.search(body[:256]) is not None


def _is_benign_upload_index(path: Path, data: bytes) -> bool:
    if path.name.lower() != "index.php" or len(data) > 2048:
        return False
    lowered = data.lower()
    if b"silence is golden" not in lowered:
        return False
    dangerous = re.compile(
        br"(?:\$_(?:GET|POST|REQUEST|COOKIE)\s*\[|\b(?:eval|assert|base64_decode|gzinflate|system|exec|shell_exec|passthru|include|require)\b)",
        re.I,
    )
    return dangerous.search(data) is None


def _find_php_payload(path: Path) -> tuple[int, str] | None:
    chunk_size = 256 * 1024
    overlap = 4096
    carry = b""
    absolute = 0

    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            data = carry + chunk
            base = max(0, absolute - len(carry))
            for match in STRICT_PHP_TOKEN.finditer(data):
                window = data[match.start() : match.start() + 4096]
                if _looks_like_php_source(window, match.group(0)):
                    start = max(0, match.start() - 48)
                    end = min(len(data), match.start() + 512)
                    return base + match.start(), _safe_preview(data[start:end])
            carry = data[-overlap:]
            absolute += len(chunk)
    return None


def _score_php_archive_member(data: bytes) -> tuple[int, list[str]]:
    """Static malware score for PHP stored inside an upload archive.

    This deliberately requires multiple behavioral indicators before treating an
    archive as malicious. A normal plugin ZIP may contain PHP, but should not
    combine heavy obfuscation, persistence, account manipulation and file writes.
    """
    score = 0
    reasons: list[str] = []

    if ARCHIVE_OBFUSCATION.search(data):
        score += 25
        reasons.append("compressed/encoded payload decoder")
    if ARCHIVE_EXECUTION.search(data):
        score += 30
        reasons.append("dynamic/system execution primitive")
    if ARCHIVE_USER_PERSISTENCE.search(data):
        score += 25
        reasons.append("WordPress user/account manipulation")
    if ARCHIVE_CRON_PERSISTENCE.search(data):
        score += 20
        reasons.append("WordPress cron persistence")
    if ARCHIVE_FILE_MUTATION.search(data):
        score += 20
        reasons.append("filesystem mutation/opcache invalidation")
    if ARCHIVE_REMOTE_IO.search(data):
        score += 25
        reasons.append("remote/network I/O")

    random_function_count = len(ARCHIVE_RANDOM_FUNCTION.findall(data))
    if random_function_count >= 8:
        score += 25
        reasons.append(f"heavy identifier obfuscation ({random_function_count} long random function names)")

    return min(score, 100), reasons


def _scan_zip_for_malicious_php(path: Path) -> tuple[list[str], list[dict[str, object]], str]:
    php_entries: list[str] = []
    suspicious: list[dict[str, object]] = []
    preview = ""

    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or not ARCHIVE_EXECUTABLE.search(name):
                    continue
                php_entries.append(name)
                if len(php_entries) > MAX_ARCHIVE_PHP_FILES:
                    break

                try:
                    with archive.open(info) as fh:
                        data = fh.read(MAX_ARCHIVE_MEMBER_BYTES)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue

                member_score, reasons = _score_php_archive_member(data)
                if member_score >= 60:
                    suspicious.append(
                        {
                            "entry": name,
                            "score": member_score,
                            "reasons": reasons,
                            "bytes_scanned": len(data),
                        }
                    )
                    if not preview:
                        preview = _safe_preview(data[:800])
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return php_entries, suspicious, preview

    return php_entries, suspicious, preview


def scan_uploads(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        signals: list[Signal] = []
        score = 0
        suffix = path.suffix.lower()
        name = path.name.lower()
        metadata: dict[str, object] = {"suffix": suffix, "size": path.stat().st_size}
        preview = ""
        action_override: str | None = None

        try:
            with path.open("rb") as fh:
                head = fh.read(262144)
        except OSError:
            continue

        # Clean-rebuild policy: ZIP packages under uploads are never copied into
        # the sanitized restore set. The immutable original backup remains intact.
        if suffix == ".zip":
            signals.append(
                Signal(
                    "uploads.archive_restore_policy",
                    30,
                    "ZIP archive is preserved in the original backup but excluded from the clean restore set by policy.",
                )
            )
            score += 30
            action_override = "DROP FROM CLEAN RESTORE (ORIGINAL BACKUP KEPT)"
            metadata["restore_policy"] = "drop"

            php_entries, suspicious_entries, zip_preview = _scan_zip_for_malicious_php(path)
            if php_entries:
                metadata["archive_php_entries"] = php_entries
            if suspicious_entries:
                signals.append(
                    Signal(
                        "uploads.archive_malicious_php",
                        70,
                        "ZIP contains PHP with multiple malware-like behaviors (obfuscation/persistence/file mutation/execution).",
                    )
                )
                score += 70
                metadata["archive_suspicious_entries"] = suspicious_entries
                action_override = "QUARANTINE / DROP FROM CLEAN RESTORE (ORIGINAL BACKUP KEPT)"
                preview = zip_preview

        if suffix in EXECUTABLE_SUFFIXES:
            if _is_benign_upload_index(path, head):
                continue
            signals.append(Signal("uploads.executable_extension", 70, "Executable PHP-like file exists under uploads."))
            score += 70

        if DOUBLE_EXT.search(name):
            signals.append(Signal("uploads.double_extension", 30, "Filename combines a media extension with an executable extension."))
            score += 30

        if suffix in MEDIA_SUFFIXES:
            try:
                payload = _find_php_payload(path)
            except OSError:
                payload = None
            if payload:
                offset, preview = payload
                signals.append(
                    Signal(
                        "uploads.php_content",
                        80,
                        "Media file contains a PHP tag in a text-like region with plausible PHP code.",
                    )
                )
                score += 80
                metadata["php_offset"] = offset
                metadata["magic_type"] = _magic_type(head[:64])
        elif suffix in EXECUTABLE_SUFFIXES:
            if SHORT_PHP_TOKEN.search(head):
                signals.append(Signal("uploads.php_content", 35, "Executable file contains a PHP opening token."))
                score += 35
        elif suffix in TEXT_SUFFIXES:
            if STRICT_PHP_TOKEN.search(head) and PHP_CODE_HINT.search(head):
                signals.append(Signal("uploads.php_content", 50, "Text-like upload contains PHP code."))
                score += 50

        score = min(score, 100)
        if score >= 30:
            try:
                metadata["sha256"] = _sha256(path)
            except OSError:
                pass
            findings.append(
                Finding(
                    "uploads",
                    str(path),
                    score,
                    severity_for(score),
                    signals,
                    preview=preview,
                    metadata=metadata,
                    action_override=action_override,
                )
            )
    return findings
