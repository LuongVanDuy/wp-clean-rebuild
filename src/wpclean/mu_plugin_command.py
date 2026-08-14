from __future__ import annotations

from pathlib import Path

import typer

from .cli import app, console, _profile_transport
from .mu_plugin_restore import run_mu_plugin_stage
from .site_config import load_site_profile


@app.command("rebuild-mu-plugins-config")
def rebuild_mu_plugins_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),
    backup_root: Path | None = typer.Argument(
        None,
        help="Verified backup root. Defaults to ./backups/<host>.",
    ),
) -> None:
    """Quét MU-plugin trong backup và chỉ upload component vượt qua cổng an toàn."""
    profile = load_site_profile(config)
    backup_root = backup_root or Path("backups") / profile.host
    report_path = Path("reports") / profile.host / "rebuild-execute.json"
    transport, _remote_root = _profile_transport(config)

    console.print(f"Site: {profile.host}")
    console.print(f"Remote WordPress root: {profile.remote_path}")
    console.print("[bold cyan]MU-PLUGIN ONLY — không wipe, không cài lại WordPress, không import database.[/bold cyan]")

    with console.status("[cyan]Đang quét và khôi phục MU-plugin an toàn...[/cyan]", spinner="dots") as status:
        def progress(event: dict) -> None:
            if event.get("phase") != "upload_mu_plugin":
                return
            current = str(event.get("current", ""))
            if len(current) > 65:
                current = "…" + current[-64:]
            status.update(f"[cyan]Đang upload MU-plugin sạch: {current}[/cyan]")

        result = run_mu_plugin_stage(
            profile=profile,
            transport=transport,
            backup_root=backup_root,
            report_path=report_path,
            progress=progress,
        )

    console.print("\n[bold green]MU-PLUGIN STAGE ĐÃ QUÉT XONG[/bold green]")
    console.print(f"Component phát hiện: {result.inventory_count}")
    console.print(f"Component sạch: {result.clean_components}")
    console.print(f"Component bị chặn: {result.blocked_components}")
    console.print(f"File đã upload: {result.files_uploaded}")

    for component in result.components:
        if not component.blocked:
            continue
        console.print(f"\n[yellow]KHÔNG UPLOAD:[/yellow] {component.name}")
        for finding in component.findings:
            if finding.score >= 60:
                console.print(
                    f" - [red]{finding.severity} {finding.score}/100[/red] {finding.path}: "
                    + "; ".join(finding.reasons)
                )
        for unreadable in component.unreadable_files[:10]:
            console.print(f" - [red]{unreadable}[/red]")

    console.print(f"Report: {report_path.with_name('mu-plugin-stage.json')}")
    if not result.completed:
        raise typer.Exit(code=2)


__all__ = ["rebuild_mu_plugins_config"]
