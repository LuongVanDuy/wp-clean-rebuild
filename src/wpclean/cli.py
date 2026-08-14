from __future__ import annotations

import platform
import sys
from pathlib import Path

import typer
from rich.console import Console

from .backup import verify_manifest, write_manifest
from .scanners import scan_sql as run_sql_scan
from .scanners import scan_uploads as run_upload_scan
from .ui import show_findings

app = typer.Typer(no_args_is_help=True, help="WordPress clean rebuild and malware triage CLI.")
console = Console()


@app.command()
def doctor() -> None:
    """Check local runtime."""
    console.print(f"Python: {sys.version.split()[0]}")
    console.print(f"Platform: {platform.platform()}")
    console.print("[green]CLI runtime looks usable.[/green]")


@app.command("scan-sql")
def scan_sql(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Scan an SQL dump offline. Never modifies the dump."""
    findings = run_sql_scan(path)
    show_findings(findings)
    raise typer.Exit(code=1 if findings else 0)


@app.command("scan-uploads")
def scan_uploads(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Scan a local uploads directory. Never deletes files."""
    findings = run_upload_scan(path)
    show_findings(findings)
    raise typer.Exit(code=1 if findings else 0)


@app.command("manifest")
def manifest(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Create manifest.json with SHA-256 hashes for a completed backup directory."""
    out = write_manifest(path)
    console.print(f"[green]Manifest written:[/green] {out}")


@app.command("verify-backup")
def verify_backup(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Verify backup files against manifest.json."""
    ok, problems = verify_manifest(path)
    if ok:
        console.print("[green]Backup verification passed.[/green]")
        return
    console.print("[red]Backup verification failed.[/red]")
    for problem in problems:
        console.print(f" - {problem}")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
