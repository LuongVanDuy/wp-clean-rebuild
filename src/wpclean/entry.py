from __future__ import annotations

import os
from pathlib import Path

import typer

from .clean_builder import build_clean_restore
from .cli import app, console, _profile_transport
from .rebuild_preflight import run_rebuild_preflight
from .site_config import load_site_profile


@app.command("prepare-clean-config")
def prepare_clean_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
    admin_user: str = typer.Option("admin", "--admin-user", help="Only WordPress administrator username to create."),
    admin_email: str | None = typer.Option(None, "--admin-email", help="Defaults to admin@<host>."),
) -> None:
    """Build a sanitized restore set without modifying the immutable original backup."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    if not backup_root.exists() or not backup_root.is_dir():
        raise typer.BadParameter(f"Backup root does not exist: {backup_root}")

    ftp_password = (
        profile.password
        or os.getenv("WPCLEAN_FTP_PASSWORD")
        or typer.prompt("FTP password (also used for the new WordPress admin as requested)", hide_input=True)
    )

    console.print(f"Original backup: {backup_root}")
    console.print(f"Clean staging: {backup_root / 'clean'}")
    console.print("[yellow]Credential reuse warning: the new WordPress admin will use the FTP password as requested.[/yellow]")
    console.print("[cyan]The plaintext password will not be printed or written to the clean report/SQL.[/cyan]")

    try:
        report = build_clean_restore(
            backup_root,
            ftp_password=ftp_password,
            host=profile.host,
            admin_username=admin_user,
            admin_email=admin_email,
        )
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]Clean staging blocked:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print("\n[green]Clean restore staging completed and SHA-256 verification passed.[/green]")
    console.print(f"Uploads copied: {report.uploads_copied}")
    console.print(f"Uploads dropped/quarantined: {report.uploads_dropped}")
    for item in report.dropped_files:
        console.print(f" - [yellow]DROP[/yellow] {item['path']}: {item['reason']}")

    console.print(f"Clean database: {report.database_clean}")
    console.print(f"WordPress table prefix: {report.table_prefix}")
    console.print(f"Only admin user: {report.admin_username}")
    console.print(f"Admin email: {report.admin_email}")
    console.print("Admin password: [bold]same as FTP password[/bold] (not displayed)")
    console.print(f"Clean manifest: {report.clean_manifest}")
    console.print("[green]Original backup was not modified.[/green]")


@app.command("rebuild-preflight")
def rebuild_preflight(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Skip duplicate recursive remote inventory; verify local recovery artifacts and configured remote root only.",
    ),
) -> None:
    """Verify recovery artifacts and produce a non-destructive rebuild gate."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    if not backup_root.exists() or not backup_root.is_dir():
        raise typer.BadParameter(f"Backup root does not exist: {backup_root}")

    transport, remote_root = _profile_transport(config)
    report_path = Path("reports") / profile.host / "rebuild-preflight.json"

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {remote_root}")
    console.print(f"Original backup: {backup_root}")
    console.print(f"Clean staging: {backup_root / 'clean'}")
    console.print("[cyan]This command is read-only on the remote site. Nothing will be deleted or uploaded.[/cyan]")
    if fast:
        console.print("[yellow]FAST mode: recursive remote scan is skipped. The execute stage will enumerate once while wiping.[/yellow]")

    with console.status("[cyan]Starting rebuild preflight...[/cyan]", spinner="dots") as status:
        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "verify_original":
                status.update("[cyan]Verifying original backup SHA-256...[/cyan]")
                return
            if phase == "verify_clean":
                status.update("[cyan]Verifying clean staging SHA-256...[/cyan]")
                return
            if phase == "verify_remote_root":
                status.update("[cyan]Verifying configured remote WordPress root...[/cyan]")
                return
            if phase == "inventory_start":
                status.update("[cyan]Scanning remote WordPress filesystem over FTP...[/cyan]")
                return
            if phase == "inventory":
                inventory_phase = event.get("inventory_phase")
                if inventory_phase == "discover":
                    dirs = event.get("dirs_scanned", 0)
                    files = event.get("files_found", 0)
                    current = str(event.get("current_dir", ""))
                    if len(current) > 70:
                        current = "…" + current[-69:]
                    status.update(
                        f"[cyan]Remote scan: dirs={dirs} | files={files} | {current}[/cyan]"
                    )
                elif inventory_phase == "discovered":
                    files = event.get("files_found", 0)
                    dirs = event.get("dirs_scanned", 0)
                    status.update(
                        f"[cyan]Remote inventory complete: dirs={dirs} | files={files}. Classifying...[/cyan]"
                    )
                return
            if phase == "classify":
                status.update(
                    f"[cyan]Classifying {event.get('files_found', 0)} remote files against backup coverage...[/cyan]"
                )
                return
            if phase == "complete_fast":
                status.update("[cyan]Fast preflight complete.[/cyan]")
                return
            if phase == "complete":
                status.update(
                    f"[cyan]Preflight analysis complete: files={event.get('files_found', 0)} | blocked={event.get('blocked_files', 0)}[/cyan]"
                )

        try:
            report = run_rebuild_preflight(
                host=profile.host,
                transport=transport,
                remote_root=remote_root,
                backup_root=backup_root,
                report_path=report_path,
                progress=on_progress,
                fast=fast,
            )
        except (ValueError, RuntimeError) as exc:
            console.print(f"[red]Rebuild preflight blocked:[/red] {exc}")
            raise typer.Exit(code=2) from exc

    console.print("\n[green]✓ Original backup verified[/green]")
    console.print("[green]✓ Clean staging verified[/green]")
    console.print("[green]✓ Configured remote WordPress root verified[/green]")
    console.print(f"Preflight mode: {report.mode.upper()}")

    if report.mode == "full":
        console.print(f"Remote files inventoried: {report.remote_files}")
        console.print(f"Planned wipe: {report.wipe_files}")
        console.print(f"Preserve: {report.preserve_files}")
        console.print(f"Blocked/unknown: {report.blocked_files}")

        if report.preserve:
            console.print("\n[bold]Preserved remote paths:[/bold]")
            for item in report.preserve[:20]:
                console.print(f" - [cyan]{item.path}[/cyan]: {item.reason}")
            if len(report.preserve) > 20:
                console.print(f" - ... and {len(report.preserve) - 20} more (see report)")

        if report.blocked:
            console.print("\n[red]DESTRUCTIVE REBUILD REMAINS LOCKED.[/red]")
            console.print("Unknown, unbacked, or drifted files were found:")
            for item in report.blocked[:30]:
                console.print(f" - [red]{item.path}[/red]: {item.reason}")
            if len(report.blocked) > 30:
                console.print(f" - ... and {len(report.blocked) - 30} more (see report)")
            console.print(f"Full report: {report_path}")
            raise typer.Exit(code=2)
    else:
        console.print("Remote recursive inventory: SKIPPED")
        console.print("Wipe scope: everything inside configured WordPress root except explicit hosting-preserve paths")

    console.print("\n[bold green]PREFLIGHT PASS — destructive rebuild may be unlocked in the next stage.[/bold green]")
    console.print(f"Preflight report: {report_path}")
    for warning in report.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")


__all__ = ["app"]
