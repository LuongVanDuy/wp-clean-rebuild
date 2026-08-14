from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from .backup import verify_manifest, write_manifest
from .db_bridge import export_database_via_php_bridge
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


def _run_backup_with_progress(transport: FTPTransport, remote_root: str, out: Path, resume: bool):
    current_stage = {"name": "starting"}

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[files]}"),
        TextColumn("{task.fields[bytes]}"),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=8,
    ) as progress_ui:
        task_id = progress_ui.add_task("Preparing backup", total=1, files="", bytes="")

        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            stage = event.get("stage", current_stage["name"])

            if phase == "stage":
                current_stage["name"] = stage
                progress_ui.update(task_id, description=f"[{stage}] discovering files", total=1, completed=0, files="", bytes="")
                return
            if phase == "discover":
                dirs = event.get("dirs_scanned", 0)
                found = event.get("files_found", 0)
                progress_ui.update(task_id, description=f"[{stage}] scanning directories", files=f"dirs {dirs} | files {found}", bytes="")
                return
            if phase == "discovered":
                total_files = event.get("files_found", 0)
                total_bytes = event.get("bytes_total", 0)
                progress_ui.update(task_id, description=f"[{stage}] downloading", total=max(total_files, 1), completed=0, files=f"0/{total_files} files", bytes=f"0 B / {_human_bytes(total_bytes)}" if total_bytes else "")
                return
            if phase == "transfer":
                total_files = event.get("files_total", 0)
                completed = event.get("files_completed", 0)
                transferred = event.get("bytes_downloaded", 0)
                total_bytes = event.get("bytes_total", 0)
                progress_ui.update(task_id, description=f"[{stage}] downloading", total=max(total_files, 1), completed=completed, files=f"{completed}/{total_files} files", bytes=f"{_human_bytes(transferred)} / {_human_bytes(total_bytes)}" if total_bytes else _human_bytes(transferred))
                return
            if phase == "complete":
                total_files = event.get("files_total", 0)
                transferred = event.get("bytes_downloaded", 0)
                progress_ui.update(task_id, description=f"[{stage}] complete", total=max(total_files, 1), completed=max(total_files, 1), files=f"{total_files}/{total_files} files", bytes=_human_bytes(transferred))
                return
            if phase == "stage_skipped":
                progress_ui.update(task_id, description=f"[{stage}] skipped", total=1, completed=1, files="", bytes="")
                return
            if phase == "config_file":
                name = event.get("file", "config")
                status = event.get("status", "")
                progress_ui.update(task_id, description=f"[config] {name}: {status}", total=1, completed=1, files="", bytes="")
                return
            if phase == "verify":
                progress_ui.update(task_id, description="[manifest] calculating and verifying SHA-256", total=None, files="", bytes="")
                return
            if phase == "verified":
                ok = event.get("verified", False)
                progress_ui.update(task_id, description="[manifest] verification passed" if ok else "[manifest] verification failed", total=1, completed=1, files="", bytes="")

        return backup_wordpress_ftp(transport, remote_root, out, resume=resume, progress=on_progress)


def _run_db_backup_with_progress(profile, transport: FTPTransport, out_path: Path):
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[bytes]}"),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=8,
    ) as progress_ui:
        task_id = progress_ui.add_task("Preparing database bridge", total=None, bytes="")

        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "upload_bridge":
                progress_ui.update(task_id, description="Uploading temporary database bridge", total=None, bytes="")
            elif phase == "request_dump":
                progress_ui.update(task_id, description="Requesting database dump", total=None, bytes="")
            elif phase == "download":
                downloaded = event.get("bytes_downloaded", 0)
                total = event.get("bytes_total")
                progress_ui.update(
                    task_id,
                    description="Downloading database",
                    total=total,
                    completed=downloaded if total else 0,
                    bytes=(f"{_human_bytes(downloaded)} / {_human_bytes(total)}" if total else _human_bytes(downloaded)),
                )
            elif phase == "remove_bridge":
                removed = event.get("removed", False)
                progress_ui.update(task_id, description="Temporary bridge removed" if removed else "WARNING: bridge removal failed", total=1, completed=1, bytes="")

        return export_database_via_php_bridge(profile, transport, out_path, progress=on_progress)


@app.command()
def doctor() -> None:
    console.print(f"Python: {sys.version.split()[0]}")
    console.print(f"Platform: {platform.platform()}")
    console.print("[green]CLI runtime looks usable.[/green]")


@app.command("scan-sql")
def scan_sql(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    findings = run_sql_scan(path)
    show_findings(findings)
    raise typer.Exit(code=1 if findings else 0)


@app.command("scan-uploads")
def scan_uploads(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    findings = run_upload_scan(path)
    show_findings(findings)
    raise typer.Exit(code=1 if findings else 0)


@app.command("manifest")
def manifest(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    out = write_manifest(path)
    console.print(f"[green]Manifest written:[/green] {out}")


@app.command("verify-backup")
def verify_backup(path: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
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
    password = os.getenv("WPCLEAN_FTP_PASSWORD") or typer.prompt("FTP password", hide_input=True)
    cfg = FTPConfig(host=host, username=username, password=password, port=port, tls=tls, passive=passive, workers=1)
    pwd = FTPTransport(cfg).test_connection()
    mode = "FTPS" if tls else "FTP"
    console.print(f"[green]{mode} connection OK.[/green] Remote cwd: {pwd}")
    if not tls:
        console.print("[yellow]Warning: plain FTP sends credentials without transport encryption.[/yellow]")


@app.command("ftp-test-config")
def ftp_test_config(config: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
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
    remote_root: str = typer.Option(..., "--remote-root"),
    out: Path = typer.Option(..., "--out"),
    port: int = typer.Option(21, "--port"),
    tls: bool = typer.Option(True, "--tls/--plain-ftp"),
    passive: bool = typer.Option(True, "--passive/--active"),
    workers: int = typer.Option(6, "--workers", min=1, max=16),
    block_mb: int = typer.Option(1, "--block-mb", min=1, max=8),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
) -> None:
    password = os.getenv("WPCLEAN_FTP_PASSWORD") or typer.prompt("FTP password", hide_input=True)
    if not tls:
        console.print("[yellow]Warning: plain FTP is unencrypted. Prefer --tls whenever the host supports FTPS.[/yellow]")
    cfg = FTPConfig(host=host, username=username, password=password, port=port, tls=tls, passive=passive, workers=workers, block_size=block_mb * 1024 * 1024)
    transport = FTPTransport(cfg)
    pwd = transport.test_connection()
    console.print(f"Connected. Remote cwd: {pwd}")
    console.print(f"Transfer profile: workers={workers}, block={block_mb} MiB, resume={resume}, passive={passive}")
    report = _run_backup_with_progress(transport, remote_root, out, resume)
    _print_backup_report(report)


@app.command("backup-config")
def backup_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path | None = typer.Option(None, "--out"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
) -> None:
    profile = load_site_profile(config)
    transport, remote_root = _profile_transport(config)
    out = out or Path("backups") / profile.host
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")
    pwd = transport.test_connection()
    console.print(f"Connected to {profile.host}:{profile.port}. Remote cwd: {pwd}")
    console.print(f"WordPress root: {remote_root}")
    console.print(f"Transfer profile: workers={profile.workers}, block={profile.block_mb} MiB, resume={resume}, passive={profile.passive}")
    console.print(f"Local backup: {out}")
    report = _run_backup_with_progress(transport, remote_root, out, resume)
    _print_backup_report(report)


@app.command("db-backup-config")
def db_backup_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path | None = typer.Option(None, "--out", help="SQL output path. Defaults to ./backups/<host>/database/original.sql"),
) -> None:
    profile = load_site_profile(config)
    transport, _ = _profile_transport(config)
    out = out or Path("backups") / profile.host / "database" / "original.sql"

    console.print(f"Database backup target: {out}")
    console.print(f"Website URL: {profile.web_base_url}")
    console.print("[yellow]A temporary PHP bridge will be uploaded and removed automatically.[/yellow]")

    result = _run_db_backup_with_progress(profile, transport, out)

    console.print(f"[green]Database backup completed:[/green] {result.sql_path}")
    console.print(f"Size: {_human_bytes(result.bytes_downloaded)}")
    console.print(f"SHA-256: {result.sha256}")
    console.print("[green]Temporary database bridge cleanup was attempted automatically.[/green]")

    backup_root = out.parent.parent
    if backup_root.exists():
        manifest_path = write_manifest(backup_root)
        ok, problems = verify_manifest(backup_root, manifest_path)
        if ok:
            console.print(f"[green]Full backup manifest regenerated and verification passed:[/green] {manifest_path}")
        else:
            console.print("[red]Backup manifest verification failed after database export.[/red]")
            for problem in problems:
                console.print(f" - {problem}")
            raise typer.Exit(code=2)


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
        console.print("[cyan]Database is not exported by FTP. Run db-backup-config for the database stage.[/cyan]")
        return
    console.print("[red]Backup verification failed. Destructive rebuild must remain locked.[/red]")
    for problem in report.verification_problems:
        console.print(f" - {problem}")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
