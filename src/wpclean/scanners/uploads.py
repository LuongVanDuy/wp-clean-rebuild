from __future__ import annotations

import re
from pathlib import Path

from ..models import Finding, Signal
from ..risk import severity_for

EXECUTABLE_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".pdf", ".mp4", ".mov", ".mp3", ".wav"}
PHP_TOKEN = re.compile(br"<\?(?:php|=)?", re.I)
DOUBLE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|pdf)\.(?:php\d*|phtml|phar)$", re.I)


def scan_uploads(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        signals: list[Signal] = []
        score = 0
        suffix = path.suffix.lower()
        name = path.name.lower()
        if suffix in EXECUTABLE_SUFFIXES:
            signals.append(Signal("uploads.executable_extension", 70, "Executable PHP-like file exists under uploads."))
            score += 70
        if DOUBLE_EXT.search(name):
            signals.append(Signal("uploads.double_extension", 30, "Filename combines a media extension with an executable extension."))
            score += 30
        try:
            head = path.read_bytes()[:65536]
        except OSError:
            continue
        if PHP_TOKEN.search(head):
            weight = 80 if suffix in MEDIA_SUFFIXES else 35
            signals.append(Signal("uploads.php_content", weight, "File content contains a PHP opening token."))
            score += weight
        score = min(score, 100)
        if score >= 30:
            findings.append(Finding("uploads", str(path), score, severity_for(score), signals, metadata={"suffix": suffix, "size": path.stat().st_size}))
    return findings
