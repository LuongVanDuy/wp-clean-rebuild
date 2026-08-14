from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

import typer
from rich.table import Table

from .cli import console
from .entry import app
from .site_config import load_site_profile


PROJECT_ROOT = Path.cwd().resolve()
SITES_DIR = PROJECT_ROOT / "sites"
BACKUPS_DIR = PROJECT_ROOT / "backups"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPAIRS_DIR = PROJECT_ROOT / "repairs"
COMPLETED_STATUSES = {"PASS", "PASS WITH WARNINGS"}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _completed_projects() -> list[dict[str, object]]:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    projects: list[dict[str, object]] = []
    for profile_path in sorted(SITES_DIR.glob("*.json"), key=lambda item: item.name.lower()):
        if profile_path.name.endswith(".example.json") or profile_path.name.endswith(".local.json"):
            continue
        try:
            profile = load_site_profile(profile_path)
        except Exception:
            continue
        final_path = REPORTS_DIR / profile.host / "final-verify.json"
        final = _read_json(final_path)
        status = str(final.get("status") or "").strip().upper()
        if status not in COMPLETED_STATUSES:
            continue
        projects.append(
            {
                "name": profile_path.stem,
                "profile": profile_path,
                "host": profile.host,
                "status": status,
                "backup": BACKUPS_DIR / profile.host,
                "reports": REPORTS_DIR / profile.host,
                "repairs": REPAIRS_DIR / profile.host,
            }
        )
    return projects


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _on_remove_error(func, path, _exc_info) -> None:
    target = Path(path)
    try:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        raise


def _delete_local_path(path: Path, allowed_root: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    if not _is_within(path, allowed_root):
        raise RuntimeError(f"Từ chối xóa đường dẫn nằm ngoài vùng dự án: {path}")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_on_remove_error)
    else:
        path.unlink()
    return True


@app.command("xoa-du-an")
def xoa_du_an() -> None:
    """Xóa dữ liệu local của một dự án đã hoàn tất; không kết nối hoặc thay đổi hosting."""
    console.print("\n[bold cyan]XÓA DỰ ÁN ĐÃ HOÀN TẤT[/bold cyan]")
    console.print("[cyan]Chức năng này chỉ xóa dữ liệu LOCAL của WP Clean Rebuild. Hosting không bị thay đổi.[/cyan]")

    projects = _completed_projects()
    if not projects:
        console.print("[yellow]Không có dự án nào đã Final Verify PASS để xóa.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("STT", justify="right")
    table.add_column("Dự án")
    table.add_column("Domain")
    table.add_column("Final Verify")
    for index, project in enumerate(projects, start=1):
        table.add_row(
            str(index),
            str(project["name"]),
            str(project["host"]),
            str(project["status"]),
        )
    console.print(table)

    choice = typer.prompt("Chọn STT dự án cần xóa", type=int)
    if choice < 1 or choice > len(projects):
        console.print("[red]Lựa chọn không hợp lệ. Không có dữ liệu nào bị xóa.[/red]")
        raise typer.Exit(code=2)

    project = projects[choice - 1]
    project_name = str(project["name"])
    host = str(project["host"])

    console.print("\n[bold yellow]DỮ LIỆU LOCAL SẼ BỊ XÓA VĨNH VIỄN:[/bold yellow]")
    console.print(f" - Cấu hình: [bold]{project['profile']}[/bold]")
    console.print(f" - Backup: [bold]{project['backup']}[/bold]")
    console.print(f" - Báo cáo: [bold]{project['reports']}[/bold]")
    console.print(f" - Bản sửa kỹ thuật: [bold]{project['repairs']}[/bold]")
    console.print("[green]Hosting / website live: KHÔNG XÓA, KHÔNG SỬA.[/green]")

    typed = typer.prompt(
        f"Để xác nhận, nhập chính xác tên dự án '{project_name}'",
        default="",
        show_default=False,
    ).strip()
    if typed != project_name:
        console.print("[yellow]Tên xác nhận không khớp. Đã hủy xóa dự án.[/yellow]")
        return

    if not typer.confirm(f"Xóa vĩnh viễn toàn bộ dữ liệu local của {project_name} ({host})?", default=False):
        console.print("[yellow]Đã hủy xóa dự án.[/yellow]")
        return

    removed: list[str] = []
    # Delete heavy/project state first. Keep sites/<project>.json until the end so
    # the operator can still reopen the project if a local deletion fails midway.
    for key, allowed_root in (
        ("backup", BACKUPS_DIR),
        ("reports", REPORTS_DIR),
        ("repairs", REPAIRS_DIR),
    ):
        path = Path(project[key])
        if _delete_local_path(path, allowed_root):
            removed.append(str(path))

    profile_path = Path(project["profile"])
    if _delete_local_path(profile_path, SITES_DIR):
        removed.append(str(profile_path))

    console.print("\n[bold green]✓ ĐÃ XÓA DỰ ÁN LOCAL THÀNH CÔNG.[/bold green]")
    console.print(f"Dự án: {project_name} | Domain: {host}")
    if removed:
        for path in removed:
            console.print(f" - Đã xóa: {path}")
    console.print("[green]Website trên hosting không bị tác động.[/green]")


__all__ = ["xoa_du_an"]
