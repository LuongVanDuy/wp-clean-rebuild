from __future__ import annotations

from pathlib import Path

import typer

from .cli import app, console, _profile_transport
from .live_verify import save_live_verify_report, verify_live_site
from .site_config import load_site_profile


@app.command("verify-live-config")
def verify_live_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Original backup root, shown for evidence/reference. Defaults to ./backups/<host>.",
    ),
) -> None:
    """Read-only final verification of the rebuilt live WordPress site."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    execution_report = Path("reports") / profile.host / "rebuild-execute.json"
    final_report = Path("reports") / profile.host / "final-verify.json"
    transport, _remote_root = _profile_transport(config)

    console.print(f"Site: {profile.host}")
    console.print(f"Website URL: {profile.web_base_url}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print(f"Original backup evidence: {backup_root}")
    console.print("[bold cyan]FINAL VERIFY — READ ONLY. Không wipe, không upload, không sửa database.[/bold cyan]")
    if not profile.use_tls:
        console.print("[yellow]Warning: profile protocol=ftp uses unencrypted transport.[/yellow]")

    with console.status("[cyan]Starting final verification...[/cyan]", spinner="dots") as status:
        def on_progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "inventory":
                status.update("[cyan]Đang inventory toàn bộ file live qua FTP...[/cyan]")
            elif phase == "core_checksums":
                status.update(
                    f"[cyan]Đang lấy checksum WordPress chính thức cho version {event.get('version', '')}...[/cyan]"
                )
            elif phase == "core_hash":
                status.update(
                    f"[cyan]Đang verify WordPress core: {event.get('completed', 0)}/{event.get('total', 0)} files[/cyan]"
                )
            elif phase == "malware_scan_start":
                status.update(
                    f"[cyan]Đang chuẩn bị scan runtime code: {event.get('total', 0)} files[/cyan]"
                )
            elif phase == "malware_scan":
                current = str(event.get("current", ""))
                if len(current) > 58:
                    current = "…" + current[-57:]
                status.update(
                    f"[cyan]Runtime malware scan: {event.get('completed', 0)}/{event.get('total', 0)} | {current}[/cyan]"
                )
            elif phase == "http":
                status.update("[cyan]Đang kiểm tra HTTP frontend và /wp-admin/...[/cyan]")

        try:
            result = verify_live_site(
                profile=profile,
                transport=transport,
                report_path=execution_report,
                progress=on_progress,
            )
        except Exception as exc:
            console.print(f"\n[bold red]FINAL VERIFY STOPPED:[/bold red] {type(exc).__name__}: {exc}")
            console.print("[yellow]Không có thay đổi nào được thực hiện trên website.[/yellow]")
            raise typer.Exit(code=2) from exc

    save_live_verify_report(result, final_report)

    console.print("\n[bold]FINAL VERIFY SUMMARY[/bold]")
    console.print(f"Remote files inventoried: {result.remote_files}")
    console.print(f"WordPress version: {result.wordpress_version or 'unknown'}")
    if result.core_expected:
        console.print(
            f"Core checksum: verified={result.core_verified}/{result.core_expected} | "
            f"missing={result.core_missing} | mismatched={result.core_mismatched} | unreadable={result.core_unreadable}"
        )
    else:
        console.print("Core checksum: [yellow]not fully verified[/yellow]")
    console.print(f"Runtime code files scanned: {result.scanned_code_files}")
    console.print(f"Known malware marker hits: {result.suspicious_markers}")
    console.print(f"Executable files under uploads: {result.uploads_executables}")
    console.print(f"Temporary bridge/data artifacts: {result.temp_artifacts}")
    console.print(f"Unexpected executable files in root: {result.unexpected_root_php}")

    if result.http_home:
        home_style = "green" if result.http_home.ok else "red"
        console.print(
            f"Frontend HTTP: [{home_style}]{'PASS' if result.http_home.ok else 'FAIL'}[/{home_style}] "
            f"status={result.http_home.status} final={result.http_home.final_url or result.http_home.url}"
        )
    if result.http_admin:
        admin_style = "green" if result.http_admin.ok else "red"
        console.print(
            f"/wp-admin HTTP: [{admin_style}]{'PASS' if result.http_admin.ok else 'FAIL'}[/{admin_style}] "
            f"status={result.http_admin.status} final={result.http_admin.final_url or result.http_admin.url}"
        )

    if result.issues:
        console.print("\n[bold red]Blocking findings:[/bold red]")
        for issue in result.issues[:50]:
            console.print(f" - [red]{issue.category}[/red] {issue.path}: {issue.detail}")
        if len(result.issues) > 50:
            console.print(f" - ... and {len(result.issues) - 50} more; xem report.")

    if result.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for warning in result.warnings:
            console.print(f" - [yellow]{warning}[/yellow]")

    console.print(f"Final report: {final_report}")

    if result.status == "PASS":
        console.print("\n[bold green]✅ FINAL VERIFY PASS — không phát hiện indicator đáng ngờ.[/bold green]")
        return
    if result.status == "PASS WITH WARNINGS":
        console.print("\n[bold yellow]⚠️ FINAL VERIFY PASS WITH WARNINGS — site hoạt động nhưng còn mục chưa xác minh đầy đủ.[/bold yellow]")
        return

    console.print("\n[bold red]❌ FINAL VERIFY BLOCKED — còn dấu hiệu cần kỹ thuật xử lý trước khi bàn giao.[/bold red]")
    raise typer.Exit(code=2)


__all__ = ["verify_live_config"]
