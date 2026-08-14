from __future__ import annotations

import re
from pathlib import Path

from ..models import Finding, Signal
from ..risk import severity_for


RULES: list[tuple[re.Pattern[str], int, str, str]] = [
    (re.compile(r"\beval\s*\(", re.I), 30, "php.eval", "Contains eval(), often abused to execute injected code."),
    (re.compile(r"\bbase64_decode\s*\(", re.I), 15, "php.base64_decode", "Contains base64_decode(); suspicious only when combined with other signals."),
    (re.compile(r"\bgzinflate\s*\(", re.I), 20, "php.gzinflate", "Contains compressed-code expansion often used in obfuscation."),
    (re.compile(r"\bstr_rot13\s*\(", re.I), 15, "php.str_rot13", "Contains string obfuscation via str_rot13()."),
    (re.compile(r"<iframe[^>]+(?:display\s*:\s*none|width\s*=\s*[\"']?0)", re.I), 25, "html.hidden_iframe", "Contains a hidden iframe."),
    (re.compile(r"<script[^>]+src\s*=\s*[\"']https?://", re.I), 20, "html.remote_script", "Loads JavaScript from an external origin; requires domain review."),
    (re.compile(r"\b(?:shell_exec|passthru|system|exec)\s*\(", re.I), 25, "php.command_exec", "Contains OS command execution primitive."),
]


def scan_sql(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            signals: list[Signal] = []
            score = 0
            for regex, weight, name, reason in RULES:
                if regex.search(line):
                    signals.append(Signal(name=name, score=weight, reason=reason))
                    score += weight
            if len(signals) >= 2:
                score += 10
                signals.append(Signal("compound.obfuscation", 10, "Multiple independent suspicious signals appear in the same SQL record/line."))
            score = min(score, 100)
            if score >= 30:
                preview = line.strip()
                if len(preview) > 500:
                    preview = preview[:500] + "…"
                findings.append(Finding("database", f"{path}:{line_no}", score, severity_for(score), signals, preview, {"line": line_no}))
    return findings
