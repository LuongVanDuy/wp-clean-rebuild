from __future__ import annotations

import json
from pathlib import Path

import typer

from .cli import console
from .plugin_restore import (
    PluginStageReport,
    classification_to_dict,
    classify_plugins,
    install_wordpress_org_plugin,
    inventory_backup_plugins,
)


def _persist_plugin_report(report_path: Path, stage: PluginStageReport) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    else:
        payload = {}
    payload["plugin_stage"] = stage.to_dict()
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    standalone = report_path.with_name("plugin-stage.json")
    standalone.write_text(
        json.dumps(stage.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return standalone


def run_plugin_stage(
    *,
    profile,
    transport,
    backup_root: Path,
    report_path: Path,
) -> PluginStageReport:
    stage = PluginStageReport()
    console.print("\n[bold cyan]PLUGIN RESTORE STAGE[/bold cyan]")
    console.print("[cyan]Backup plugin chỉ dùng để nhận diện danh sách; code plugin cũ sẽ KHÔNG được restore.[/cyan]")

    inventory = inventory_backup_plugins(backup_root)
    stage.inventory_count = len(inventory)
    stage.inventory = [
        {
            "slug": item.slug,
            "name": item.name,
            "version": item.version,
            "kind": item.kind,
            "main_file": item.main_file,
            "source_path": item.source_path,
            "candidate_slugs": item.candidate_slugs,
        }
        for item in inventory
    ]

    if not inventory:
        console.print("[yellow]Không phát hiện plugin nào trong backup.[/yellow]")
        _persist_plugin_report(report_path, stage)
        return stage

    console.print(f"Plugins phát hiện trong backup: [bold]{len(inventory)}[/bold]")
    with console.status("[cyan]Đang kiểm tra plugin trên WordPress.org...[/cyan]", spinner="dots") as status:
        def lookup_progress(event: dict) -> None:
            if event.get("phase") != "plugin_lookup":
                return
            status.update(
                f"[cyan]WordPress.org lookup: {event.get('completed', 0)}/{event.get('total', 0)} | "
                f"{event.get('slug', '')} → {event.get('status', '')}[/cyan]"
            )

        classifications = classify_plugins(
            inventory,
            workers=getattr(profile, "workers", 6),
            progress=lookup_progress,
        )

    stage.classifications = [classification_to_dict(item) for item in classifications]
    public = [item for item in classifications if item.status == "wordpress.org" and item.wporg is not None]
    manual = [item for item in classifications if item.status == "manual"]
    lookup_errors = [item for item in classifications if item.status == "lookup-error"]
    stage.wordpress_org_count = len(public)
    stage.manual_count = len(manual)
    stage.lookup_error_count = len(lookup_errors)

    if public:
        console.print("\n[bold green]Có trên WordPress.org — hỗ trợ cài bản sạch mới nhất:[/bold green]")
        for item in public:
            assert item.wporg is not None
            old_version = f" | backup {item.inventory.version}" if item.inventory.version else ""
            console.print(
                f" - [green]✓[/green] {item.wporg.name} "
                f"([bold]{item.inventory.slug}[/bold]) → WordPress.org {item.wporg.version}{old_version}"
            )

    if manual:
        console.print("\n[bold yellow]Không có trên WordPress.org — cần cài thủ công:[/bold yellow]")
        for item in manual:
            console.print(
                f" - [yellow]⚠[/yellow] {item.inventory.name} ([bold]{item.inventory.slug}[/bold])"
            )
        console.print(
            "[bold yellow]Hãy upload bản sạch lấy từ nhà cung cấp/nguồn tin cậy. "
            "Không dùng lại plugin code trong backup của website đã nhiễm.[/bold yellow]"
        )

    if lookup_errors:
        console.print("\n[bold red]Không xác minh được do lỗi WordPress.org/network:[/bold red]")
        for item in lookup_errors:
            console.print(f" - [red]{item.inventory.name} ({item.inventory.slug})[/red]: {item.detail}")
        stage.warnings.append(
            "Some plugin WordPress.org lookups failed; these plugins were not installed and were not classified as private."
        )

    if not public:
        console.print("\n[yellow]Không có plugin WordPress.org nào sẵn sàng để tự động cài.[/yellow]")
        standalone = _persist_plugin_report(report_path, stage)
        console.print(f"Plugin report: {standalone}")
        return stage

    stage.install_prompted = True
    install_public = typer.confirm(
        f"Bạn có muốn tải và cài {len(public)} plugin sạch từ WordPress.org không?",
        default=True,
    )
    if not install_public:
        stage.warnings.append("User declined automatic WordPress.org plugin installation.")
        console.print("[yellow]Đã bỏ qua cài plugin WordPress.org theo lựa chọn của bạn.[/yellow]")
        standalone = _persist_plugin_report(report_path, stage)
        console.print(f"Plugin report: {standalone}")
        return stage

    stage.install_accepted = True
    console.print("\n[bold cyan]Bắt đầu cài plugin sạch từ WordPress.org...[/bold cyan]")
    for index, item in enumerate(public, start=1):
        assert item.wporg is not None
        console.print(
            f"\n[cyan][{index}/{len(public)}][/cyan] {item.wporg.name} "
            f"([bold]{item.inventory.slug}[/bold])"
        )
        try:
            with console.status(
                f"[cyan]Downloading/installing {item.wporg.name}...[/cyan]",
                spinner="dots",
            ) as status:
                def install_progress(event: dict) -> None:
                    phase = event.get("phase")
                    if phase == "plugin_download":
                        status.update(f"[cyan]Downloading {item.wporg.name} from WordPress.org...[/cyan]")
                    elif phase == "plugin_upload_start":
                        status.update(f"[cyan]Uploading clean {item.wporg.name}...[/cyan]")
                    elif phase == "upload_wporg_plugin":
                        current = str(event.get("current", ""))
                        if len(current) > 55:
                            current = "…" + current[-54:]
                        status.update(
                            f"[cyan]Uploading {item.wporg.name}: "
                            f"{event.get('files_completed', 0)}/{event.get('files_total', 0)} | {current}[/cyan]"
                        )

                installed = install_wordpress_org_plugin(
                    profile,
                    transport,
                    item,
                    progress=install_progress,
                )
            stage.installed.append(
                {
                    "source_slug": installed.source_slug,
                    "wporg_slug": installed.wporg_slug,
                    "name": installed.name,
                    "version": installed.version,
                    "remote_slug": installed.remote_slug,
                    "files_uploaded": installed.files_uploaded,
                    "package_sha256": installed.package_sha256,
                    "download_link": installed.download_link,
                }
            )
            stage.installed_count += 1
            console.print(
                f"[green]✓ Installed {installed.name} {installed.version}: "
                f"{installed.files_uploaded} files | SHA-256 {installed.package_sha256}[/green]"
            )
        except Exception as exc:
            warning = f"Plugin install failed for {item.inventory.slug}: {type(exc).__name__}: {exc}"
            stage.warnings.append(warning)
            console.print(f"[red]✗ {warning}[/red]")
            console.print("[yellow]Tiếp tục plugin kế tiếp; có thể chạy plugin-only stage lại sau.[/yellow]")

    standalone = _persist_plugin_report(report_path, stage)
    console.print("\n[bold green]PLUGIN STAGE COMPLETED[/bold green]")
    console.print(f"WordPress.org plugins installed: {stage.installed_count}/{stage.wordpress_org_count}")
    if stage.manual_count:
        console.print(f"Manual/private plugins cần cài thủ công: {stage.manual_count}")
    if stage.lookup_error_count:
        console.print(f"Plugin lookup cần thử lại: {stage.lookup_error_count}")
    console.print(f"Plugin report: {standalone}")
    return stage


__all__ = ["run_plugin_stage"]
