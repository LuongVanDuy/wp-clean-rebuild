from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import typer
from rich.console import Console

from .backup import verify_manifest, write_manifest
from .remote_backup import backup_wordpress_ftp
from .scanners import scan_sql as run_sql_scan
from .scanners import scan_uploads as run_upload_scan
from .site_config import load_site_profile
from .transport import FTPConfig, FTPTransport
from .ui import show_findings

app = typer.Typer(no_args_is_help=True, help="WordPress clean rebuild and malware triage CLI.")
console = Console()


def _human_bytes(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def _profile_transport(config_path: Path) -> tuple[FTPTransport, str]:
    profile = load_site_profile(config_path)
    password = profile.password or os.getenv("WPCLEAN_FTP_PASSWORD") or typer.prompt("FTP password", hide_input=True)
    cfg = FTPConfig(
        host=profile.host,
        username=profile.username,
        password=password,
        port=profile.port,
        tls=profile.use_tls,
        passive=profile.passive,
        workers=profile.workers,
        block_size=profile.block_mb * 1024 * 1024,
    )
    return FTPTransport(cfg), profile.remote_path


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


@app.command("ftp-test")
def ftp_test(
    host: str = typer.Option(..., "--host"),
    username: str = typer.Option(..., "--user"),
    port: int = typer.Option(21, "--port"),
    tls: bool = typer.Option(True, "--tls/--plain-ftp"),
    passive: bool = typer.Option(True, "--passive/--active"),
) -> None:
    """Test FTP/FTPS credentials without writing to the server."""
    password = os.getenv("WPCLEAN_FTP_PASSWORD") or typer.prompt("FTP password", hide_input=True)
    cfg = FTPConfig(
        host=host,
        username=username,
        password=password,
        port=port,
        tls=tls,
        passive=passive,
        workers=1,
    )
    pwd = FTPTransport(cfg).test_connection()
    mode = "FTPS" if tls else "FTP"
    console.print(f"[green]{mode} connection OK.[/green] Remote cwd: {pwd}")
    if not tls:
        console.print("[yellow]Warning: plain FTP sends credentials without transport encryption.[/yellow]")


@app.command("ftp-test-config")
def ftp_test_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Test a JSON site profile using host/username/password/protocol/port/remotePath."""
    profile = load_site_profile(config)
    transport, remote_root = _profile_transport(config)
    pwd = transport.test_connection()
    mode = "FTPS" if profile.use_tls else "FTP"
    console.print(f"[green]{mode} connection OK.[/green]")
    console.print(f"Host: {profile.host}:{profile.port}")
    console.print(f"Remote cwd: {pwd}")
    console.print(f"WordPress root configured: {remote_root}")
    if not profile.use_tls:
        console.print("[yellow]Warning: this profile uses plain FTP; credentials/data are not transport-encrypted.[/yellow]")


@app.command("backup-ftp")
def backup_ftp(
    host: str = typer.Option(..., "--host"),
    username: str = typer.Option(..., "--user"),
    remote_root: str = typer.Option(..., "--remote-root", help="WordPress root, e.g. /public_html"),
    out: Path = typer.Option(..., "--out", help="Local backup directory"),
    port: int = typer.Option(21, "--port"),
    tls: bool = typer.Option(True, "--tls/--plain-ftp"),
    passive: bool = typer.Option(True, "--passive/--active"),
    workers: int = typer.Option(6, "--workers", min=1, max=16),
    block_mb: int = typer.Option(1, "--block-mb", min=1, max=8),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
) -> None:
    """Back up WordPress files over high-throughput FTP/FTPS and verify SHA-256 manifest."""
    password = os.getenv("WPCLEAN_FTP_PASSWORD") or typer.prompt("FTP password", hide_input=True)
    if not tls:
        console.print("[yellow]Warning: plain FTP is unencrypted. Prefer --tls whenever the host supports FTPS.[/yellow]")

    cfg = FTPConfig(
        host=host,
        username=username,
        password=password,
        port=port,
        tls=tls,
        passive=passive,
        workers=workers,
        block_size=block_mb * 1024 * 1024,
    )
    transport = FTPTransport(cfg)
    pwd = transport.test_connection()
    console.print(f"Connected. Remote cwd: {pwd}")
    console.print(f"Transfer profile: workers={workers}, block={block_mb} MiB, resume={resume}, passive={passive}")

    report = backup_wordpress_ftp(transport, remote_root, out, resume=resume)
    _print_backup_report(report)


@app.command("backup-config")
def backup_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path | None = typer.Option(None, "--out", help="Local backup directory. Defaults to ./backups/<host>"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
) -> None:
    """Back up WordPress using a JSON site connection profile."""
    profile = load_site_profile(config)
    transport, remote_root = _profile_transport(config)
    out = out or Path("backups") / profile.host

    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")

    pwd = transport.test_connection()
    console.print(f"Connected to {profile.host}:{profile.port}. Remote cwd: {pwd}")
    console.print(f"WordPress root: {remote_root}")
    console.print(
        f"Transfer profile: workers={profile.workers}, block={profile.block_mb} MiB, "
        f"resume={resume}, passive={profile.passive}"
    )
    console.print(f"Local backup: {out}")

    report = backup_wordpress_ftp(transport, remote_root, out, resume=resume)
    _print_backup_report(report)


def _print_backup_report(report) -> None:
    total_files = sum(item.files_total for item in report.items)
    downloaded = sum(item.files_downloaded for item in report.items)
    skipped = sum(item.files_skipped for item in report.items)
    transferred = sum(item.bytes_downloaded for item in report.items)

    console.print(f"Files discovered: {total_files}")
    console.print(f"Downloaded: {downloaded}; resumed/already complete: {skipped}")
    console.print(f"Transferred this run: {_human_bytes(transferred)}")
    console.print(f"Manifest: {report.manifest_path}")

    missing = [item for item in report.items if item.status != "ok"]
    for item in missing:
        console.print(f"[yellow]Skipped {item.remote_path}: {item.error}[/yellow]")

    if report.verified:
        console.print("[green]Filesystem backup completed and SHA-256 verification passed.[/green]")
        console.print("[cyan]Database is not exported by FTP. Use the database adapter in the next stage.[/cyan]")
        return

    console.print("[red]Backup verification failed. Destructive rebuild must remain locked.[/red]")
    for problem in report.verification_problems:
        console.print(f" - {problem}")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
