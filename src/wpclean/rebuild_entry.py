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
    install_parent_choice: bool | None = None,
    install_child_choice: bool | None = None,
) -> ThemeStageResult:
    """Run the trusted theme stage.

    ``None`` keeps the existing terminal prompt behavior. GUI callers can pass
    explicit booleans so the same engine runs without stdin interaction.
    """
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
    install_parent = (
        typer.confirm("Bạn có muốn cài Flatsome sạch từ themes/flatsome.zip không?", default=True)
        if install_parent_choice is None
        else bool(install_parent_choice)
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
    install_child = (
        typer.confirm(f"Bạn có muốn cài lại theme con {active.stylesheet} không?", default=False)
        if install_child_choice is None
        else bool(install_child_choice)
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

    child_scan = scan_child_theme(scan_root, slug=active.stylesheet, backup_root=scan_backup_root)
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
            console.print(f" - [{style}]{finding.severity.value} {finding.score}/100[/{style}] {display_path}: {reasons}")

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
        _save_theme_stage(report_path, result)
        return result

    console.print("[green]✓ Theme con scan PASS: không phát hiện HIGH/CRITICAL malware indicator và không có file unreadable.[/green]")
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

        child_uploaded = install_child_theme(profile, transport, scan_root, active.stylesheet, progress=child_progress)
    result.child_installed = True
    result.child_files_uploaded = child_uploaded
    console.print(f"[green]✓ Child theme {active.stylesheet} installed: {child_uploaded} files[/green]")
    _save_theme_stage(report_path, result)
    return result


@app.command("rebuild-config")
def rebuild_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(None, help="Verified backup root. Defaults to ./backups/<host>."),
    execute: bool = typer.Option(False, "--execute", help="Actually wipe and rebuild the configured WordPress root. Without this flag the command only prints the plan."),
    restore_backup_code: bool = typer.Option(False, "--restore-backup-code", help="Explicitly restore backed-up plugins/themes/mu-plugins after wipe. This can reintroduce compromised PHP."),
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
        console.print("  6. Do not restore compromised-backup plugins/themes" if not restore_backup_code else "  6. Restore backed-up plugins/themes/mu-plugins by explicit override")
        console.print("  7. Import clean/database/clean.sql through a temporary authenticated bridge with detailed diagnostics")
        console.print("  8. Remove temporary import bridge/data and write execution report")
        console.print("  9. Detect active theme; install trusted Flatsome; child themes use immutable backup + repair workspace gate")
        console.print(" 10. Inventory backup plugins; reinstall only WordPress.org plugins from fresh official packages; list private plugins for manual clean upload")
        return
    transport, _remote_root = _profile_transport(config)
    report = execute_rebuild(
        profile=profile,
        transport=transport,
        backup_root=backup_root,
        preflight_path=preflight_path,
        report_path=report_path,
        restore_backup_code=restore_backup_code,
    )
    if not report.completed:
        raise typer.Exit(code=2)
    _run_theme_stage(profile=profile, transport=transport, backup_root=backup_root, report_path=report_path)
    plugin_result = run_plugin_stage(profile=profile, transport=transport, backup_root=backup_root, report_path=report_path)
    if plugin_result.lookup_error_count or plugin_result.installed_count < plugin_result.wordpress_org_count:
        raise typer.Exit(code=2)


@app.command("rebuild-resume-db-config")
def rebuild_resume_db_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(None),
) -> None:
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _ = _profile_transport(config)
    result = resume_database_import(profile=profile, transport=transport, backup_root=backup_root, report_path=report_path)
    console.print(f"DATABASE IMPORT RESUME COMPLETED — statements={result.statements}")


@app.command("rebuild-theme-config")
def rebuild_theme_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(None),
) -> None:
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _ = _profile_transport(config)
    _run_theme_stage(profile=profile, transport=transport, backup_root=backup_root, report_path=report_path)


@app.command("rebuild-plugin-config")
def rebuild_plugin_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(None),
) -> None:
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _ = _profile_transport(config)
    result = run_plugin_stage(profile=profile, transport=transport, backup_root=backup_root, report_path=report_path)
    console.print(f"Plugins: inventory={result.inventory_count} wporg={result.wordpress_org_count} installed={result.installed_count} manual={result.manual_count} lookup_error={result.lookup_error_count}")
