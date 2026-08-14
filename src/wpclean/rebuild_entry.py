from __future__ import annotations

from pathlib import Path
import threading

import typer

from .cli import console, _profile_transport
from .entry import app
from .rebuild_execute import execute_rebuild
from .site_config import load_site_profile


@app.command("rebuild-config")
def rebuild_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Actually wipe and rebuild the configured WordPress root. Without this flag the command only prints the plan.",
    ),
    restore_backup_code: bool = typer.Option(
        False,
        "--restore-backup-code",
        help="Explicitly restore backed-up plugins/themes/mu-plugins after wipe. This can reintroduce compromised PHP.",
    ),
) -> None:
    """Wipe the configured WordPress root and rebuild it from verified clean recovery artifacts."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    preflight_path = Path("reports") / profile.host / "rebuild-preflight.json"
    report_path = Path("reports") / profile.host / "rebuild-execute.json"

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print(f"Original backup: {backup_root}")
    console.print(f"Clean staging: {backup_root / 'clean'}")
    console.print(f"Preflight report: {preflight_path}")

    if not execute:
        console.print("\n[yellow]DRY ARM ONLY — nothing was changed remotely.[/yellow]")
        console.print("Execution plan:")
        console.print("  1. Verify original backup + clean staging again")
        console.print("  2. Download/extract fresh WordPress core before the destructive boundary")
        console.print("  3. Wipe everything inside the configured WordPress root except .well-known")
        console.print("  4. Upload fresh WordPress core + fresh wp-config.php + clean WordPress .htaccess")
        console.print("  5. Restore clean/uploads")
        if restore_backup_code:
            console.print("  6. [red]Restore backed-up plugins/themes/mu-plugins by explicit override[/red]")
        else:
            console.print("  6. Do not restore compromised-backup plugins/themes")
        console.print("  7. Import clean/database/clean.sql through a temporary authenticated bridge")
        console.print("  8. Remove temporary import bridge/data and write execution report")
        console.print("\nTo execute, rerun with [bold]--execute[/bold].")
        return

    transport, _remote_root = _profile_transport(config)
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")
    console.print("\n[bold red]DESTRUCTIVE EXECUTION ENABLED.[/bold red]")
    console.print("The configured WordPress root will be wiped after all local recovery artifacts and the fresh core package verify.")
    if restore_backup_code:
        console.print(
            "[bold red]Override enabled: backed-up plugins/themes/mu-plugins will be restored even though they came from the compromised site.[/bold red]"
        )

    update_lock = threading.Lock()
    with console.status("[cyan]Preparing rebuild...[/cyan]", spinner="dots") as status:
        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            with update_lock:
                if phase == "verify_original":
                    status.update("[cyan]Verifying original backup SHA-256...[/cyan]")
                elif phase == "verify_clean":
                    status.update("[cyan]Verifying clean staging SHA-256...[/cyan]")
                elif phase == "download_core":
                    status.update("[cyan]Downloading fresh WordPress core from wordpress.org...[/cyan]")
                elif phase == "extract_core":
                    status.update("[cyan]Validating and extracting fresh WordPress core locally...[/cyan]")
                elif phase == "destructive_boundary":
                    status.update("[bold red]Recovery artifacts ready. Entering destructive boundary...[/bold red]")
                elif phase == "wipe":
                    current = str(event.get("current", ""))
                    if len(current) > 62:
                        current = "…" + current[-61:]
                    status.update(
                        f"[red]Wiping remote: files={event.get('deleted_files', 0)} | dirs={event.get('deleted_dirs', 0)} | {current}[/red]"
                    )
                elif phase.startswith("upload_") and phase not in {"db_import_upload"}:
                    label = phase.removeprefix("upload_").replace("_", " ")
                    current = str(event.get("current", ""))
                    if len(current) > 55:
                        current = "…" + current[-54:]
                    status.update(
                        f"[cyan]Uploading {label}: {event.get('files_completed', 0)}/{event.get('files_total', 0)} | {current}[/cyan]"
                    )
                elif phase == "db_import_upload":
                    status.update("[cyan]Uploading sanitized database to temporary import staging...[/cyan]")
                elif phase == "db_import_execute":
                    status.update("[cyan]Importing sanitized database...[/cyan]")
                elif phase == "db_import_cleanup":
                    status.update(
                        "[cyan]Cleaning temporary database bridge/data: "
                        f"bridge={event.get('bridge_removed')} data={event.get('data_removed')}[/cyan]"
                    )
                elif phase == "complete":
                    status.update("[green]Rebuild execution completed.[/green]")

        try:
            report = execute_rebuild(
                profile=profile,
                transport=transport,
                backup_root=backup_root,
                preflight_path=preflight_path,
                report_path=report_path,
                restore_backup_code=restore_backup_code,
                progress=on_progress,
            )
        except Exception as exc:
            console.print(f"\n[bold red]REBUILD STOPPED:[/bold red] {exc}")
            console.print(f"Execution state report: {report_path}")
            console.print("[yellow]Do not rerun blindly; inspect the report to see whether the destructive boundary had already been crossed.[/yellow]")
            raise typer.Exit(code=2) from exc

    console.print("\n[bold green]REBUILD COMPLETED[/bold green]")
    console.print(f"WordPress version: {report.wordpress_version}")
    console.print(f"WordPress package SHA-256: {report.wordpress_package_sha256}")
    console.print(f"Remote wiped: {report.wiped_files} files, {report.wiped_dirs} directories")
    if report.preserved_paths:
        console.print(f"Preserved hosting paths: {', '.join(report.preserved_paths)}")
    console.print(f"Fresh core uploaded: {report.core_uploaded} files")
    console.print(f"Clean uploads restored: {report.uploads_uploaded} files")
    console.print(f"Fresh wp-config.php uploaded: {report.wp_config_uploaded}")
    console.print(f"Clean .htaccess uploaded: {report.htaccess_uploaded}")
    console.print(f"Database imported: {report.database_imported} ({report.database_statements} statements)")
    console.print(
        f"Temporary import cleanup: bridge_removed={report.temp_bridge_removed}, data_removed={report.temp_sql_removed}"
    )
    if restore_backup_code:
        console.print(
            f"Backed-up executable code restored: plugins={report.plugins_uploaded}, themes={report.themes_uploaded}, mu-plugins={report.mu_plugins_uploaded}"
        )
    else:
        console.print("[yellow]Old plugins/themes were intentionally not restored; reinstall trusted copies next.[/yellow]")
    for warning in report.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")
    console.print(f"Execution report: {report_path}")


__all__ = ["app"]
