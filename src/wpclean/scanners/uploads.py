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
ARCHIVE_DOUBLE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|pdf)\.(?:php\d*|phtml|phar)$", re.I)


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
    """Return fraction of bytes that look like ordinary source-code text."""
    if not data:
        return 0.0
    printable = sum(1 for value in data if value in (9, 10, 13) or 32 <= value <= 126)
    return printable / len(data)


def _looks_like_php_source(window: bytes, token: bytes) -> bool:
    """Reject random PHP-looking byte sequences inside compressed binary media.

    Real PHP source around a tag is overwhelmingly textual. Compressed image bytes
    can randomly contain '<?php' or '<?='; therefore a tag alone is never enough.
    """
    sample = window[:512]
    if len(sample) < 12 or _printable_ratio(sample) < 0.82:
        return False

    token_lower = token.lower()
    if token_lower.startswith(b"<?php"):
        return PHP_CODE_HINT.search(sample) is not None

    # For short echo tags require a plausible PHP expression, not merely '<?='.
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
    """Return exact byte offset and sanitized preview for credible PHP in media."""
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


def _scan_zip(path: Path) -> tuple[list[Signal], list[str]]:
    signals: list[Signal] = []
    evidence: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return signals, evidence

    executable_entries = [name for name in names if ARCHIVE_EXECUTABLE.search(name)]
    double_ext_entries = [name for name in names if ARCHIVE_DOUBLE_EXT.search(name)]

    if double_ext_entries:
        evidence = double_ext_entries[:10]
        signals.append(
            Signal(
                "uploads.archive_double_extension",
                70,
                f"ZIP contains media-looking executable file(s), e.g. {double_ext_entries[0]}",
            )
        )
    elif executable_entries:
        evidence = executable_entries[:10]
        signals.append(
            Signal(
                "uploads.archive_executable",
                40,
                f"ZIP contains PHP-like executable file(s), e.g. {executable_entries[0]}",
            )
        )
    return signals, evidence


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

        try:
            with path.open("rb") as fh:
                head = fh.read(262144)
        except OSError:
            continue

        if suffix in EXECUTABLE_SUFFIXES:
            if _is_benign_upload_index(path, head):
                continue
            signals.append(Signal("uploads.executable_extension", 70, "Executable PHP-like file exists under uploads."))
            score += 70

        if DOUBLE_EXT.search(name):
            signals.append(Signal("uploads.double_extension", 30, "Filename combines a media extension with an executable extension."))
            score += 30

        if suffix == ".zip":
            archive_signals, archive_entries = _scan_zip(path)
            signals.extend(archive_signals)
            score += sum(signal.score for signal in archive_signals)
            if archive_entries:
                metadata["archive_executable_entries"] = archive_entries
        elif suffix in MEDIA_SUFFIXES:
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
                )
            )
    return findings
