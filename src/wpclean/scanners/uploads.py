from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..models import Finding, Signal
from ..risk import severity_for

EXECUTABLE_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".pdf", ".mp4", ".mov", ".mp3", ".wav", ".ico"}
TEXT_SUFFIXES = {".txt", ".html", ".htm", ".js", ".css", ".xml", ".json", ".htaccess", ".ini"}

# Do not match a bare '<?' in binary media. Random compressed image bytes can
# contain that pair frequently enough to produce large false-positive bursts.
STRICT_PHP_TOKEN = re.compile(br"<\?(?:php\b|=)", re.I)
SHORT_PHP_TOKEN = re.compile(br"<\?(?!xml\b)", re.I)
PHP_CODE_HINT = re.compile(
    br"(?:\$_(?:GET|POST|REQUEST|COOKIE|SERVER)\s*\[|\b(?:eval|assert|base64_decode|gzinflate|str_rot13|system|exec|shell_exec|passthru|include|include_once|require|require_once|echo|print|function)\b)",
    re.I,
)
DOUBLE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|pdf)\.(?:php\d*|phtml|phar)$", re.I)
ARCHIVE_EXECUTABLE = re.compile(r"(?:^|/)[^/]+\.(?:php\d*|phtml|phar)$", re.I)
ARCHIVE_DOUBLE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|pdf)\.(?:php\d*|phtml|phar)$", re.I)


def _is_benign_upload_index(path: Path, data: bytes) -> bool:
    """Recognize the common no-op index.php guard used to prevent directory listing."""
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


def _php_payload_in_media(data: bytes) -> bool:
    """Require a real PHP opening tag plus nearby PHP-like syntax for binary media."""
    for match in STRICT_PHP_TOKEN.finditer(data):
        window = data[match.start() : match.start() + 4096]
        if match.group(0).lower().startswith(b"<?="):
            # Short echo tags are already highly specific and should not occur in media.
            return True
        if PHP_CODE_HINT.search(window):
            return True
    return False


def _scan_zip(path: Path) -> list[Signal]:
    signals: list[Signal] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return signals

    executable_entries = [name for name in names if ARCHIVE_EXECUTABLE.search(name)]
    double_ext_entries = [name for name in names if ARCHIVE_DOUBLE_EXT.search(name)]

    if double_ext_entries:
        signals.append(
            Signal(
                "uploads.archive_double_extension",
                70,
                f"ZIP contains media-looking executable file(s), e.g. {double_ext_entries[0]}",
            )
        )
    elif executable_entries:
        signals.append(
            Signal(
                "uploads.archive_executable",
                40,
                f"ZIP contains PHP-like executable file(s), e.g. {executable_entries[0]}",
            )
        )
    return signals


def scan_uploads(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        signals: list[Signal] = []
        score = 0
        suffix = path.suffix.lower()
        name = path.name.lower()

        try:
            # 256 KiB is enough for a useful first-pass payload check while keeping scans fast.
            head = path.read_bytes()[:262144]
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
            archive_signals = _scan_zip(path)
            signals.extend(archive_signals)
            score += sum(signal.score for signal in archive_signals)
        elif suffix in MEDIA_SUFFIXES:
            if _php_payload_in_media(head):
                signals.append(
                    Signal(
                        "uploads.php_content",
                        80,
                        "Media file contains a real PHP opening tag with nearby PHP-like code.",
                    )
                )
                score += 80
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
            findings.append(
                Finding(
                    "uploads",
                    str(path),
                    score,
                    severity_for(score),
                    signals,
                    metadata={"suffix": suffix, "size": path.stat().st_size},
                )
            )
    return findings
