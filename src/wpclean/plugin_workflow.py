from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

import typer

from .cli import console
from .plugin_restore import (
    PluginStageReport,
    classification_to_dict,
    classify_plugins,
    install_wordpress_org_plugin,
    inventory_backup_plugins,
)


ProgressCallback = Callable[[dict], None]


def plugin_install_choices(backup_root: Path) -> list[dict[str, object]]:
    """Return the local backup inventory used by the GUI selection gate."""
    return [
        {
            "slug": item.slug,
            "name": item.name or item.slug,
            "version": item.version,
            "kind": item.kind,
        }
        for item in inventory_backup_plugins(backup_root)
    ]


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
    selected_slugs: Iterable[str] | None = None,
    progress: ProgressCallback | None = None,
) -> PluginStageReport:
    stage = PluginStageReport()
    console.print("\n[bold cyan]PLUGIN RESTORE STAGE[/bold cyan]")
    console.print("[cyan]Backup plugin chỉ dùng để nhận diện danh sách; code plugin cũ sẽ KHÔNG được restore.[/cyan]")

    full_inventory = inventory_backup_plugins(backup_root)
    stage.inventory_count = len(full_inventory)
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
        for item in full_inventory
    ]

    if not full_inventory:
        stage.selection_confirmed = True
        console.print("[yellow]Không phát hiện plugin nào trong backup.[/yellow]")
        _persist_plugin_report(report_path, stage)
        return stage

    if selected_slugs is None:
        inventory = full_inventory
    else:
        requested = {str(slug).strip() for slug in selected_slugs if str(slug).strip()}
        available = {item.slug for item in full_inventory}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError("Plugin được chọn không có trong backup: " + ", ".join(unknown))
        inventory = [item for item in full_inventory if item.slug in requested]
        stage.selection_confirmed = True
        stage.selected_slugs = [item.slug for item in inventory]
        stage.selected_count = len(inventory)

        if not inventory:
            stage.install_accepted = True
            stage.warnings.append("Operator chose not to install any backup plugins.")
            console.print("[yellow]Đã bỏ qua cài plugin theo lựa chọn trên giao diện.[/yellow]")
            _persist_plugin_report(report_path, stage)
            return stage

    console.print(
        f"Plugins đã chọn để kiểm tra/cài: [bold]{len(inventory)}[/bold]"
        f"/{len(full_inventory)} phát hiện trong backup"
    )
    with console.status("[cyan]Đang kiểm tra plugin trên WordPress.org...[/cyan]", spinner="dots") as status:
        def lookup_progress(event: dict) -> None:
            if event.get("phase") != "plugin_lookup":
                return
            status.update(
                f"[cyan]WordPress.org lookup: {event.get('completed', 0)}/{event.get('total', 0)} | "
                f"{event.get('slug', '')} → {event.get('status', '')}[/cyan]"
            )
            if progress:
                progress(
                    {
                        **event,
                        "plugin_completed": event.get("completed", 0),
                        "plugin_total": event.get("total", len(inventory)),
                        "unit": "plugin",
                        "current": event.get("slug", ""),
                    }
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
    stage.install_target_count = len(public)
    if selected_slugs is None and not public:
        stage.selection_confirmed = True
        stage.selected_slugs = [item.inventory.slug for item in classifications]
        stage.selected_count = len(classifications)

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

    stage.install_prompted = selected_slugs is None
    install_public = selected_slugs is not None or typer.confirm(
        f"Bạn có muốn tải và cài {len(public)} plugin sạch từ WordPress.org không?", default=True
    )
    stage.selection_confirmed = True
    if not install_public:
        stage.selected_slugs = []
        stage.selected_count = 0
        stage.install_target_count = 0
        stage.warnings.append("User declined automatic WordPress.org plugin installation.")
        console.print("[yellow]Đã bỏ qua cài plugin WordPress.org theo lựa chọn của bạn.[/yellow]")
        standalone = _persist_plugin_report(report_path, stage)
        console.print(f"Plugin report: {standalone}")
        return stage

    stage.install_accepted = True
    if selected_slugs is None:
        stage.selected_slugs = [item.inventory.slug for item in classifications]
        stage.selected_count = len(classifications)
    _persist_plugin_report(report_path, stage)
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
                    if progress:
                        payload = dict(event)
                        payload.update(
                            {
                                "plugin_completed": index - 1,
                                "plugin_total": len(public),
                                "plugin_name": item.wporg.name,
                                "plugin_slug": item.inventory.slug,
                                "unit": "plugin",
                            }
                        )
                        if not payload.get("current"):
                            payload["current"] = item.wporg.name
                        progress(payload)

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
            if progress:
                progress(
                    {
                        "phase": "plugin_installed",
                        "plugin_completed": index,
                        "plugin_total": len(public),
                        "current": installed.name,
                        "unit": "plugin",
                    }
                )
            _persist_plugin_report(report_path, stage)
            console.print(
                f"[green]✓ Installed {installed.name} {installed.version}: "
                f"{installed.files_uploaded} files | SHA-256 {installed.package_sha256}[/green]"
            )
        except Exception as exc:
            warning = f"Plugin install failed for {item.inventory.slug}: {type(exc).__name__}: {exc}"
            stage.warnings.append(warning)
            if progress:
                progress(
                    {
                        "phase": "plugin_failed",
                        "plugin_completed": index,
                        "plugin_total": len(public),
                        "files_failed": 1,
                        "current": item.wporg.name,
                        "unit": "plugin",
                    }
                )
            _persist_plugin_report(report_path, stage)
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


__all__ = ["plugin_install_choices", "run_plugin_stage"]
