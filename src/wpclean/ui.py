from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import Finding

console = Console()


def _format_metadata(metadata: dict) -> str:
    rows: list[str] = []
    if "magic_type" in metadata:
        rows.append(f"magic={metadata['magic_type']}")
    if "php_offset" in metadata:
        rows.append(f"php_offset={metadata['php_offset']} bytes")
    if "sha256" in metadata:
        rows.append(f"sha256={metadata['sha256']}")
    entries = metadata.get("archive_executable_entries")
    if entries:
        rows.append("archive PHP entries:\n  - " + "\n  - ".join(str(item) for item in entries))
    return "\n".join(rows)


def show_findings(findings: list[Finding]) -> None:
    if not findings:
        console.print("[green]No findings above the current threshold.[/green]")
        return
    for i, finding in enumerate(findings, start=1):
        table = Table(show_header=False, box=None)
        table.add_row("Location", finding.location)
        table.add_row("Risk", f"{finding.score}/100 — {finding.severity.value}")
        table.add_row("Recommended", finding.recommended_action)
        reasons = "\n".join(f"[+{s.score}] {s.name}: {s.reason}" for s in finding.signals)
        if reasons:
            table.add_row("Reasons", reasons)
        evidence = _format_metadata(finding.metadata)
        if evidence:
            table.add_row("Evidence", evidence)
        if finding.preview:
            table.add_row("Preview", finding.preview)
        console.print(Panel(table, title=f"Finding #{i}"))
