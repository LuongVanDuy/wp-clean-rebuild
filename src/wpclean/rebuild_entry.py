from __future__ import annotations

import json
from pathlib import Path
import threading

import typer

from .cli import console, _profile_transport
from .entry import app
from . import rebuild_execute as rebuild_engine
from .htaccess_defaults import build_production_htaccess
from .plugin_workflow import run_plugin_stage
from .rebuild_execute import execute_rebuild
from .rebuild_resume import import_database_with_diagnostics, resume_database_import
from .site_config import load_site_profile
from .theme_restore import (
    ThemeStageResult,
    existing_child_theme_repair,
    install_child_theme,
    install_flatsome,
    plan_theme_stage,
    prepare_child_theme_repair,
    scan_child_theme,
)


# Normal rebuilds use the same production-safe defaults as the recovery path:
# - the user's confirmed LiteSpeed/Apache .htaccess template is generated fresh;
# - database HTTP/PHP/MySQL errors preserve their diagnostic response body.
# execute_rebuild resolves both names from rebuild_execute module globals at runtime.
rebuild_engine.build_clean_htaccess = build_production_htaccess
rebuild_engine._import_database = import_database_with_diagnostics


def _save_theme_stage(report_path: Path, result: ThemeStageResult) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    else:
        payload = {}
    payload["theme_stage"] = result.to_dict()
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _display_relative(location: str, root: Path) -> str:
    path = Path(location)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _run_theme_stage(
    *,
    profile,
    transport,
    backup_root: Path,
    report_path: Path,
) -> ThemeStageResult:
    result = ThemeStageResult()
    active, child_root = plan_theme_stage(backup_root)

    console.print("\n[bold cyan]THEME RESTORE STAGE[/bold cyan]")
    if active is None:
        result.mode = "detection-unavailable"
        result.warnings.append("Could not determine active theme from clean database options template/stylesheet.")
        console.print("[yellow]Không xác định được theme đang active từ clean database.[/yellow]")
        console.print("[yellow]Bỏ qua cài theme tự động; WordPress core/database vẫn giữ nguyên.[/yellow]")
        _save_theme_stage(report_path, result)
        return result

    result.template = active.template
    result.stylesheet = active.stylesheet
    console.print(f"Theme parent trong database: [bold]{active.template}[/bold]")
    console.print(f"Theme active stylesheet: [bold]{active.stylesheet}[/bold]")

    if not active.is_flatsome:
        result.mode = "unsupported"
        result.unsupported_theme = active.stylesheet or active.template
        result.warnings.append(f"Unsupported theme requires manual installation: {result.unsupported_theme}")
        console.print(
            f"[yellow]Theme {result.unsupported_theme} không nằm trong danh sách tự động hỗ trợ. "
            f"Vui lòng cài theme {result.unsupported_theme} thủ công.[/yellow]"
        )
        _save_theme_stage(report_path, result)
        return result

    result.mode = "flatsome-child" if active.has_child else "flatsome"
    result.flatsome_prompted = True
    console.print("Phát hiện website sử dụng [bold]Flatsome[/bold].")
    install_parent = typer.confirm(
        "Bạn có muốn cài Flatsome sạch từ themes/flatsome.zip không?",
        default=True,
    )
    if not install_parent:
        result.warnings.append("User declined trusted Flatsome installation.")
        console.print("[yellow]Đã bỏ qua cài Flatsome theo lựa chọn của bạn.[/yellow]")
        if active.has_child:
            result.child_theme_detected = True
            result.child_theme_slug = active.stylesheet
            result.warnings.append("Child theme was not offered because the Flatsome parent was not installed.")
            console.print(
                f"[yellow]Database đang dùng theme con {active.stylesheet}, nhưng theme cha Flatsome chưa được cài; "
                "bỏ qua theme con.[/yellow]"
            )
        _save_theme_stage(report_path, result)
        return result

    with console.status("[cyan]Validating and uploading trusted Flatsome package...[/cyan]", spinner="dots") as status:
        def parent_progress(event: dict) -> None:
            if event.get("phase") != "upload_flatsome_theme":
                return
            current = str(event.get("current", ""))
            if len(current) > 55:
                current = "…" + current[-54:]
            status.update(
                f"[cyan]Uploading Flatsome: {event.get('files_completed', 0)}/{event.get('files_total', 0)} | {current}[/cyan]"
            )

        uploaded, digest = install_flatsome(profile, transport, progress=parent_progress)
    result.flatsome_installed = True
    result.flatsome_files_uploaded = uploaded
    result.flatsome_package_sha256 = digest
    console.print(f"[green]✓ Flatsome installed from trusted package: {uploaded} files[/green]")
    console.print(f"Flatsome package SHA-256: {digest}")

    if not active.has_child:
        console.print("[green]Không phát hiện Flatsome child theme đang active. Theme stage hoàn tất.[/green]")
        _save_theme_stage(report_path, result)
        return result

    result.child_theme_detected = True
    result.child_theme_slug = active.stylesheet
    result.child_prompted = True
    console.print(f"\n[yellow]Website đang sử dụng theme con: {active.stylesheet}[/yellow]")
    install_child = typer.confirm(
        f"Bạn có muốn cài lại theme con {active.stylesheet} không?",
        default=False,
    )
    if not install_child:
        result.warnings.append(f"User declined child theme restore: {active.stylesheet}")
        console.print("[yellow]Đã bỏ qua theme con. Không thực hiện scan hoặc upload theme con.[/yellow]")
        _save_theme_stage(report_path, result)
        return result

    assert child_root is not None
    repair_root = existing_child_theme_repair(backup_root, active.stylesheet)
    if repair_root is not None:
        scan_root = repair_root
        scan_backup_root = None
        result.child_scan_source = "repair-working-copy"
        result.child_repair_workspace = str(repair_root)
        console.print(f"[cyan]Phát hiện bản theme kỹ thuật đang sửa: {repair_root}[/cyan]")
        console.print("Đang quét lại working-copy; backup gốc sẽ không bị sửa hoặc ghi đè.")
    else:
        scan_root = child_root
        scan_backup_root = backup_root
        result.child_scan_source = "immutable-backup"
        console.print("Đang quét theme con từ immutable backup trước khi cho phép upload...")

    child_scan = scan_child_theme(
        scan_root,
        slug=active.stylesheet,
        backup_root=scan_backup_root,
    )
    result.child_scan = child_scan.to_dict()
    console.print(f"Child-theme files scanned: {child_scan.files_scanned}")

    if child_scan.unreadable_files:
        console.print("[red]File không đọc được / backup không đầy đủ:[/red]")
        for path in child_scan.unreadable_files[:20]:
            console.print(f" - [red]{path}[/red]")

    if child_scan.findings:
        console.print("[bold yellow]File nghi vấn cần kỹ thuật kiểm tra:[/bold yellow]")
        for finding in child_scan.findings:
            style = "red" if finding.score >= 60 else "yellow"
            reasons = "; ".join(signal.reason for signal in finding.signals)
            display_path = _display_relative(finding.location, scan_root)
            console.print(
                f" - [{style}]{finding.severity.value} {finding.score}/100[/{style}] "
                f"{display_path}: {reasons}"
            )

    if child_scan.blocked:
        working_copy, created = prepare_child_theme_repair(
            backup_root,
            child_root,
            child_scan,
            scan_root=scan_root,
        )
        result.child_repair_workspace = str(working_copy)
        result.child_repair_created = created
        result.warnings.append("Child theme restore blocked; repair workspace requires technical review.")

        console.print("\n[bold red]KHÔNG UPLOAD THEME CON.[/bold red]")
        if created:
            console.print("[green]Đã tạo một bản working copy riêng cho kỹ thuật sửa.[/green]")
        else:
            console.print("[cyan]Working copy đã tồn tại; tool KHÔNG ghi đè các thay đổi của kỹ thuật.[/cyan]")
        console.print(f"Theme cần sửa: [bold]{working_copy}[/bold]")
        console.print(f"Danh sách file nghi vấn: [bold]{working_copy.parent / 'SUSPECT_FILES.txt'}[/bold]")
        console.print(f"Scan report: {working_copy.parent / 'scan-report.json'}")
        console.print(f"Backup gốc giữ nguyên: {child_root}")
        console.print(
            "[bold yellow]Vui lòng kỹ thuật sửa trong working-copy, sau đó chạy lại cùng lệnh. "
            "Tool sẽ quét lại working-copy; chỉ PASS mới upload theme con.[/bold yellow]"
        )
        _save_theme_stage(report_path, result)
        return result

    console.print(
        "[green]✓ Theme con scan PASS: không phát hiện HIGH/CRITICAL malware indicator và không có file unreadable.[/green]"
    )
    if result.child_scan_source == "repair-working-copy":
        console.print("[green]✓ Bản kỹ thuật sửa đã PASS; bắt đầu upload working-copy.[/green]")
    else:
        console.print("[cyan]Theme con đã được xác nhận; bắt đầu upload từ backup đã scan.[/cyan]")

    with console.status(f"[cyan]Uploading child theme {active.stylesheet}...[/cyan]", spinner="dots") as status:
        def child_progress(event: dict) -> None:
            if event.get("phase") != "upload_child_theme":
                return
            current = str(event.get("current", ""))
            if len(current) > 55:
                current = "…" + current[-54:]
            status.update(
                f"[cyan]Uploading child theme: {event.get('files_completed', 0)}/{event.get('files_total', 0)} | {current}[/cyan]"
            )

        child_uploaded = install_child_theme(
            profile,
            transport,
            scan_root,
            active.stylesheet,
            progress=child_progress,
        )
    result.child_installed = True
    result.child_files_uploaded = child_uploaded
    console.print(f"[green]✓ Child theme {active.stylesheet} installed: {child_uploaded} files[/green]")
    _save_theme_stage(report_path, result)
    return result


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
        console.print("  4. Upload fresh WordPress core + fresh wp-config.php + production LiteSpeed/Apache .htaccess")
        console.print("  5. Restore clean/uploads")
        if restore_backup_code:
            console.print("  6. [red]Restore backed-up plugins/themes/mu-plugins by explicit override[/red]")
        else:
            console.print("  6. Do not restore compromised-backup plugins/themes")
        console.print("  7. Import clean/database/clean.sql through a temporary authenticated bridge with detailed diagnostics")
        console.print("  8. Remove temporary import bridge/data and write execution report")
        console.print("  9. Detect active theme; install trusted Flatsome; child themes use immutable backup + repair workspace gate")
        console.print(" 10. Inventory backup plugins; reinstall only WordPress.org plugins from fresh official packages; list private plugins for manual clean upload")
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
                    status.update("[green]Core/database rebuild completed.[/green]")

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

    try:
        theme_result = _run_theme_stage(
            profile=profile,
            transport=transport,
            backup_root=backup_root,
            report_path=report_path,
        )
    except Exception as exc:
        failed = ThemeStageResult(mode="failed", warnings=[str(exc)])
        _save_theme_stage(report_path, failed)
        console.print(f"\n[bold red]THEME STAGE STOPPED:[/bold red] {exc}")
        console.print("[green]WordPress core + clean database rebuild had already completed successfully.[/green]")
        console.print("[yellow]Do NOT rerun destructive --execute just to retry the theme stage.[/yellow]")
        console.print(
            f"Retry theme only with: .\\wpclean.bat rebuild-theme-config {config} {backup_root}"
        )
        raise typer.Exit(code=2) from exc

    try:
        plugin_result = run_plugin_stage(
            profile=profile,
            transport=transport,
            backup_root=backup_root,
            report_path=report_path,
        )
    except Exception as exc:
        console.print(f"\n[bold red]PLUGIN STAGE STOPPED:[/bold red] {exc}")
        console.print("[green]WordPress core/database và theme stage đã chạy xong; không wipe lại website.[/green]")
        console.print("[yellow]Do NOT rerun destructive --execute chỉ để thử lại plugin.[/yellow]")
        console.print(
            f"Retry plugin only with: .\\wpclean.bat rebuild-plugin-config {config} {backup_root}"
        )
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
    console.print(f"Production .htaccess uploaded: {report.htaccess_uploaded}")
    console.print(f"Database imported: {report.database_imported} ({report.database_statements} statements)")
    console.print(
        f"Temporary import cleanup: bridge_removed={report.temp_bridge_removed}, data_removed={report.temp_sql_removed}"
    )
    if theme_result.flatsome_installed:
        console.print(
            f"Flatsome installed: True ({theme_result.flatsome_files_uploaded} files)"
        )
    if theme_result.child_theme_detected:
        console.print(
            f"Child theme {theme_result.child_theme_slug}: installed={theme_result.child_installed}"
        )
    if theme_result.child_repair_workspace:
        console.print(f"Child theme repair workspace: {theme_result.child_repair_workspace}")
    if theme_result.unsupported_theme:
        console.print(
            f"[yellow]Unsupported theme requires manual install: {theme_result.unsupported_theme}[/yellow]"
        )
    console.print(
        f"WordPress.org plugins installed: {plugin_result.installed_count}/{plugin_result.wordpress_org_count}"
    )
    if plugin_result.manual_count:
        console.print(
            f"[yellow]Private/non-WordPress.org plugins requiring manual clean upload: {plugin_result.manual_count}[/yellow]"
        )
    if plugin_result.lookup_error_count:
        console.print(
            f"[yellow]Plugin lookups requiring retry: {plugin_result.lookup_error_count}[/yellow]"
        )
    if restore_backup_code:
        console.print(
            f"Backed-up executable code restored by override: plugins={report.plugins_uploaded}, themes={report.themes_uploaded}, mu-plugins={report.mu_plugins_uploaded}"
        )
    for warning in report.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")
    for warning in theme_result.warnings:
        console.print(f"[yellow]Theme warning: {warning}[/yellow]")
    for warning in plugin_result.warnings:
        console.print(f"[yellow]Plugin warning: {warning}[/yellow]")
    console.print(f"Execution report: {report_path}")


@app.command("rebuild-theme-config")
def rebuild_theme_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
) -> None:
    """Run only the safe theme-detection/install stage; never wipe core or re-import DB."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _remote_root = _profile_transport(config)

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print("[bold cyan]THEME-ONLY MODE — no wipe, no WordPress reinstall, no database import.[/bold cyan]")
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")

    try:
        result = _run_theme_stage(
            profile=profile,
            transport=transport,
            backup_root=backup_root,
            report_path=report_path,
        )
    except Exception as exc:
        failed = ThemeStageResult(mode="failed", warnings=[str(exc)])
        _save_theme_stage(report_path, failed)
        console.print(f"[bold red]THEME STAGE STOPPED:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print("\n[bold green]THEME STAGE COMPLETED[/bold green]")
    console.print(f"Mode: {result.mode}")
    console.print(f"Flatsome installed: {result.flatsome_installed}")
    if result.child_theme_detected:
        console.print(f"Child theme: {result.child_theme_slug} | installed={result.child_installed}")
    if result.child_repair_workspace:
        console.print(f"Repair workspace: {result.child_repair_workspace}")
    if result.unsupported_theme:
        console.print(f"Manual theme required: {result.unsupported_theme}")
    console.print(f"Execution report updated: {report_path}")


@app.command("rebuild-plugin-config")
def rebuild_plugin_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
) -> None:
    """Run only plugin inventory/classification and trusted WordPress.org reinstall."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _remote_root = _profile_transport(config)

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print("[bold cyan]PLUGIN-ONLY MODE — no wipe, no WordPress reinstall, no database import, no theme changes.[/bold cyan]")
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")

    try:
        result = run_plugin_stage(
            profile=profile,
            transport=transport,
            backup_root=backup_root,
            report_path=report_path,
        )
    except Exception as exc:
        console.print(f"[bold red]PLUGIN STAGE STOPPED:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print("\n[bold green]PLUGIN-ONLY STAGE COMPLETED[/bold green]")
    console.print(f"Plugins inventoried: {result.inventory_count}")
    console.print(f"WordPress.org plugins: {result.wordpress_org_count}")
    console.print(f"Installed from WordPress.org: {result.installed_count}")
    console.print(f"Manual/private plugins: {result.manual_count}")
    console.print(f"Lookup errors: {result.lookup_error_count}")
    console.print(f"Execution report updated: {report_path}")


@app.command("rebuild-resume-db-config")
def rebuild_resume_db_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
) -> None:
    """Resume only a failed clean database import; never wipe or reinstall WordPress."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _remote_root = _profile_transport(config)

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print(f"Clean database: {backup_root / 'clean' / 'database' / 'clean.sql'}")
    console.print(f"Execution report: {report_path}")
    console.print("[bold cyan]DATABASE-ONLY RESUME — remote WordPress files will NOT be wiped or reinstalled.[/bold cyan]")
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")

    update_lock = threading.Lock()
    with console.status("[cyan]Preparing database-only resume...[/cyan]", spinner="dots") as status:
        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            with update_lock:
                if phase == "db_resume_ready":
                    status.update(
                        f"[cyan]Recovery state verified; stale temp files removed={event.get('stale_removed', 0)}.[/cyan]"
                    )
                elif phase == "db_import_upload":
                    status.update("[cyan]Uploading clean.sql to temporary authenticated import staging...[/cyan]")
                elif phase == "db_import_execute":
                    status.update("[cyan]Importing sanitized database...[/cyan]")
                elif phase == "db_import_cleanup":
                    status.update(
                        "[cyan]Cleaning temporary database bridge/data: "
                        f"bridge={event.get('bridge_removed')} data={event.get('data_removed')}[/cyan]"
                    )

        try:
            result = resume_database_import(
                profile=profile,
                transport=transport,
                backup_root=backup_root,
                execution_report_path=report_path,
                progress=on_progress,
            )
        except Exception as exc:
            console.print(f"\n[bold red]DATABASE RESUME STOPPED:[/bold red] {exc}")
            console.print(f"Execution report updated: {report_path}")
            console.print("[yellow]No remote wipe/reinstall was performed by this resume command.[/yellow]")
            raise typer.Exit(code=2) from exc

    console.print("\n[bold green]DATABASE IMPORT RESUME COMPLETED[/bold green]")
    console.print(f"SQL statements imported: {result.statements}")
    console.print(f"Stale import files removed before retry: {len(result.stale_files_removed)}")
    console.print(
        f"Temporary import cleanup: bridge_removed={result.bridge_removed}, data_removed={result.data_removed}"
    )
    console.print(f"Execution report updated: {report_path}")


__all__ = ["app"]
