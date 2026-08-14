from __future__ import annotations

import os
from pathlib import Path

import typer

from .clean_builder import build_clean_restore
from .cli import app, console
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


__all__ = ["app"]
