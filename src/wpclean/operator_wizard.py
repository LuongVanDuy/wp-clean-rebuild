from __future__ import annotations

from datetime import datetime
import getpass
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .backup import verify_manifest, write_manifest
from .clean_builder import build_clean_restore
from .db_bridge import export_database_via_php_bridge
from .live_verify import save_live_verify_report, verify_live_site
from .plugin_workflow import run_plugin_stage
from .rebuild_preflight import run_rebuild_preflight
from .remote_backup import backup_wordpress_ftp
from .scanners import scan_sql, scan_uploads
from .site_config import SiteConnectionProfile, load_site_profile
from .transport import FTPConfig, FTPTransport

# Import rebuild_entry so the production .htaccess and diagnostic DB importer
# monkeypatches are applied before execute_rebuild is called by the wizard.
from . import rebuild_execute as rebuild_engine
from .rebuild_entry import _run_theme_stage


console = Console()
PROJECT_ROOT = Path.cwd()
SITES_DIR = PROJECT_ROOT / "sites"
BACKUPS_DIR = PROJECT_ROOT / "backups"
REPORTS_DIR = PROJECT_ROOT / "reports"


class TamDungQuyTrinh(Exception):
    """Operator intentionally paused a recoverable manual step."""


class LoiBuoc(Exception):
    """A workflow stage could not safely continue."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower()).strip("-._")
    return value or "du-an"


def _profile_summary(path: Path) -> tuple[str, str, str]:
    raw = _read_json(path)
    return (
        str(raw.get("host") or path.stem),
        str(raw.get("protocol") or "?"),
        str(raw.get("remotePath") or "?"),
    )


def _project_files() -> list[Path]:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        [
            path
            for path in SITES_DIR.glob("*.json")
            if not path.name.endswith(".example.json") and not path.name.endswith(".local.json")
        ],
        key=lambda item: item.name.lower(),
    )


def _ask_protocol(default: str = "ftp") -> str:
    while True:
        value = Prompt.ask("Giao thức FTP", default=default).strip().lower()
        if value in {"ftp", "ftps", "ftp+tls", "ftp-tls"}:
            return value
        console.print("[red]Chỉ hỗ trợ: ftp hoặc ftps.[/red]")


def _ask_bool(message: str, default: bool = True) -> bool:
    return Confirm.ask(message, default=default)


def _create_project() -> Path:
    console.print("\n[bold cyan]TẠO DỰ ÁN MỚI[/bold cyan]")
    host = Prompt.ask("Tên miền / FTP host").strip()
    username = Prompt.ask("Tài khoản FTP").strip()
    password = getpass.getpass("Mật khẩu FTP: ")
    protocol = _ask_protocol("ftp")
    port = IntPrompt.ask("Cổng FTP", default=21)
    remote_default = f"/domains/{host}/public_html"
    remote_path = Prompt.ask("Thư mục WordPress trên hosting", default=remote_default).strip()
    site_url = Prompt.ask("Địa chỉ website", default=f"https://{host}").strip()
    passive = _ask_bool("Dùng FTP passive mode?", True)
    workers = IntPrompt.ask("Số luồng truyền file", default=4)
    workers = max(1, min(16, workers))
    block_mb = IntPrompt.ask("Kích thước block truyền file (MiB)", default=1)
    block_mb = max(1, min(8, block_mb))
    project_name = _slug(Prompt.ask("Tên dự án", default=host))

    path = SITES_DIR / f"{project_name}.json"
    if path.exists() and not Confirm.ask(f"{path} đã tồn tại. Ghi đè cấu hình này?", default=False):
        project_name = _slug(Prompt.ask("Nhập tên dự án khác", default=f"{project_name}-2"))
        path = SITES_DIR / f"{project_name}.json"
        if path.exists():
            raise LoiBuoc(f"File cấu hình đã tồn tại: {path}")

    payload = {
        "host": host,
        "username": username,
        "password": password,
        "protocol": protocol,
        "port": port,
        "remotePath": remote_path,
        "siteUrl": site_url,
        "passive": passive,
        "workers": workers,
        "blockMb": block_mb,
    }
    _write_json(path, payload)
    console.print(f"[green]✓ Đã tạo cấu hình dự án:[/green] {path}")
    console.print("[cyan]File sites/*.json đã được .gitignore; không đưa thông tin đăng nhập lên GitHub.[/cyan]")
    return path


def _edit_project(path: Path) -> Path:
    raw = _read_json(path)
    console.print(f"\n[bold cyan]CHỈNH SỬA CẤU HÌNH — {path.name}[/bold cyan]")
    host = Prompt.ask("Tên miền / FTP host", default=str(raw.get("host") or "")).strip()
    username = Prompt.ask("Tài khoản FTP", default=str(raw.get("username") or "")).strip()
    password = str(raw.get("password") or "")
    if not password or Confirm.ask("Bạn có muốn nhập lại mật khẩu FTP?", default=not bool(password)):
        password = getpass.getpass("Mật khẩu FTP: ")
    protocol = _ask_protocol(str(raw.get("protocol") or "ftp"))
    port = IntPrompt.ask("Cổng FTP", default=int(raw.get("port") or 21))
    remote_path = Prompt.ask(
        "Thư mục WordPress trên hosting",
        default=str(raw.get("remotePath") or f"/domains/{host}/public_html"),
    ).strip()
    site_url = Prompt.ask("Địa chỉ website", default=str(raw.get("siteUrl") or f"https://{host}")).strip()
    passive = Confirm.ask("Dùng FTP passive mode?", default=bool(raw.get("passive", True)))
    workers = max(1, min(16, IntPrompt.ask("Số luồng truyền file", default=int(raw.get("workers") or 4))))
    block_mb = max(1, min(8, IntPrompt.ask("Kích thước block truyền file (MiB)", default=int(raw.get("blockMb") or 1))))
    _write_json(
        path,
        {
            "host": host,
            "username": username,
            "password": password,
            "protocol": protocol,
            "port": port,
            "remotePath": remote_path,
            "siteUrl": site_url,
            "passive": passive,
            "workers": workers,
            "blockMb": block_mb,
        },
    )
    console.print("[green]✓ Đã cập nhật cấu hình.[/green]")
    return path


def _select_project() -> Path:
    projects = _project_files()
    console.print("\n[bold cyan]BƯỚC 2 — CHỌN DỰ ÁN[/bold cyan]")
    if projects:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("STT", justify="right")
        table.add_column("Dự án")
        table.add_column("Host")
        table.add_column("Giao thức")
        table.add_column("Thư mục WordPress")
        for index, path in enumerate(projects, start=1):
            host, protocol, remote_path = _profile_summary(path)
            table.add_row(str(index), path.stem, host, protocol.upper(), remote_path)
        table.add_row(str(len(projects) + 1), "Tạo dự án mới", "", "", "")
        console.print(table)
        choice = IntPrompt.ask("Chọn dự án", default=1)
        if choice == len(projects) + 1:
            return _create_project()
        if 1 <= choice <= len(projects):
            return projects[choice - 1]
        raise LoiBuoc("Lựa chọn dự án không hợp lệ.")
    console.print("[yellow]Chưa có dự án nào. Hệ thống sẽ tạo dự án mới.[/yellow]")
    return _create_project()


def _runtime_password(profile: SiteConnectionProfile) -> str:
    if profile.password:
        return profile.password
    value = getpass.getpass("Mật khẩu FTP cho phiên làm việc này: ")
    if not value:
        raise LoiBuoc("Mật khẩu FTP không được để trống.")
    return value


def _transport(profile: SiteConnectionProfile, password: str) -> FTPTransport:
    return FTPTransport(
        FTPConfig(
            host=profile.host,
            username=profile.username,
            password=password,
            port=profile.port,
            tls=profile.use_tls,
            passive=profile.passive,
            workers=profile.workers,
            block_size=profile.block_mb * 1024 * 1024,
        )
    )


def _paths(profile: SiteConnectionProfile, profile_path: Path) -> dict[str, Path]:
    state_path = REPORTS_DIR / profile.host / "operator-state.json"
    state = _read_json(state_path)
    remembered_backup = state.get("backup_root")
    backup_root = Path(str(remembered_backup)) if remembered_backup else BACKUPS_DIR / profile.host
    return {
        "profile": profile_path,
        "backup": backup_root,
        "state": state_path,
        "preflight": REPORTS_DIR / profile.host / "rebuild-preflight.json",
        "execute": REPORTS_DIR / profile.host / "rebuild-execute.json",
        "scan": REPORTS_DIR / profile.host / "operator-scan.json",
        "final": REPORTS_DIR / profile.host / "final-verify.json",
    }


def _save_operator_state(paths: dict[str, Path], profile: SiteConnectionProfile, **updates: Any) -> dict[str, Any]:
    state = _read_json(paths["state"])
    state.update(updates)
    state["host"] = profile.host
    state["profile"] = str(paths["profile"])
    state["backup_root"] = str(paths["backup"])
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(paths["state"], state)
    return state


def _infer_status(paths: dict[str, Path]) -> dict[str, Any]:
    backup_root = paths["backup"]
    backup_report = _read_json(backup_root / "backup-report.json")
    clean_report = _read_json(backup_root / "clean" / "clean-report.json")
    preflight = _read_json(paths["preflight"])
    execution = _read_json(paths["execute"])
    final = _read_json(paths["final"])
    operator = _read_json(paths["state"])

    filesystem_backup = bool(backup_report.get("verified")) and (backup_root / "manifest.json").is_file()
    database_backup = (backup_root / "database" / "original.sql").is_file()
    backup_ready = filesystem_backup and database_backup and (backup_root / "manifest.json").is_file()
    scan_ready = paths["scan"].is_file()
    clean_ready = bool(clean_report.get("clean_verified")) and (backup_root / "clean" / "manifest.json").is_file()
    preflight_ready = bool(preflight.get("ready_for_destructive_rebuild"))
    rebuild_ready = bool(
        execution.get("database_imported")
        and execution.get("wp_config_uploaded")
        and execution.get("htaccess_uploaded")
        and int(execution.get("core_uploaded") or 0) > 0
    )

    theme = execution.get("theme_stage") if isinstance(execution.get("theme_stage"), dict) else {}
    theme_mode = str(theme.get("mode") or "")
    child_detected = bool(theme.get("child_theme_detected"))
    child_installed = bool(theme.get("child_installed"))
    theme_done = False
    theme_manual = False
    theme_repair = False
    if rebuild_ready and theme:
        if theme_mode == "flatsome":
            theme_done = bool(theme.get("flatsome_installed"))
        elif theme_mode == "flatsome-child":
            theme_done = bool(theme.get("flatsome_installed")) and child_installed
            theme_repair = child_detected and not child_installed and bool(theme.get("child_repair_workspace"))
        elif theme_mode in {"unsupported", "detection-unavailable"}:
            theme_manual = True
            theme_done = bool(operator.get("manual_theme_ack"))

    plugin = execution.get("plugin_stage") if isinstance(execution.get("plugin_stage"), dict) else {}
    plugin_done = bool(plugin)
    plugin_public = int(plugin.get("wordpress_org_count") or 0)
    plugin_installed = int(plugin.get("installed_count") or 0)
    plugin_lookup_errors = int(plugin.get("lookup_error_count") or 0)
    plugin_manual = int(plugin.get("manual_count") or 0)
    selection_confirmed = bool(plugin.get("selection_confirmed", True))
    install_target = int(plugin.get("install_target_count", plugin_public) or 0)
    if plugin and (not selection_confirmed or plugin_installed < install_target or plugin_lookup_errors):
        plugin_done = False

    final_status = str(final.get("status") or "")
    return {
        "filesystem_backup": filesystem_backup,
        "database_backup": database_backup,
        "backup_ready": backup_ready,
        "scan_ready": scan_ready,
        "clean_ready": clean_ready,
        "preflight_ready": preflight_ready,
        "rebuild_ready": rebuild_ready,
        "theme_done": theme_done,
        "theme_manual": theme_manual,
        "theme_repair": theme_repair,
        "theme": theme,
        "plugin_done": plugin_done,
        "plugin_manual": plugin_manual,
        "plugin_lookup_errors": plugin_lookup_errors,
        "plugin": plugin,
        "manual_plugins_ack": bool(operator.get("manual_plugins_ack")),
        "final_status": final_status,
    }


def _icon(ok: bool) -> str:
    return "[green]✓[/green]" if ok else "[dim]·[/dim]"


def _show_status(profile: SiteConnectionProfile, paths: dict[str, Path], status: dict[str, Any]) -> None:
    console.print("\n[bold cyan]TRẠNG THÁI DỰ ÁN[/bold cyan]")
    table = Table(show_header=False, box=None)
    table.add_column("Trạng thái", width=3)
    table.add_column("Hạng mục")
    table.add_column("Chi tiết")
    table.add_row(_icon(status["filesystem_backup"]), "Backup file", str(paths["backup"]))
    table.add_row(_icon(status["database_backup"]), "Backup database", "database/original.sql")
    table.add_row(_icon(status["clean_ready"]), "Bộ dữ liệu sạch", "clean/")
    table.add_row(_icon(status["preflight_ready"]), "Kiểm tra trước rebuild", "sẵn sàng" if status["preflight_ready"] else "chưa chạy")
    table.add_row(_icon(status["rebuild_ready"]), "WordPress + database", "đã rebuild" if status["rebuild_ready"] else "chưa rebuild")
    theme_detail = "đã xong" if status["theme_done"] else ("đang chờ kỹ thuật sửa" if status["theme_repair"] else "chưa xong")
    table.add_row(_icon(status["theme_done"]), "Theme", theme_detail)
    plugin_detail = "đã xử lý" if status["plugin_done"] else "chưa xong"
    if status["plugin_manual"]:
        plugin_detail += f" | {status['plugin_manual']} plugin cần thủ công"
    table.add_row(_icon(status["plugin_done"]), "Plugin", plugin_detail)
    final_ok = status["final_status"] in {"PASS", "PASS WITH WARNINGS"}
    table.add_row(_icon(final_ok), "Kiểm tra cuối", status["final_status"] or "chưa chạy")
    console.print(table)
    console.print(f"Website: [bold]{profile.web_base_url}[/bold]")


def _next_stage(status: dict[str, Any]) -> str:
    if status["final_status"] in {"PASS", "PASS WITH WARNINGS"}:
        return "done"
    if not status["filesystem_backup"]:
        return "backup-files"
    if not status["database_backup"]:
        return "backup-db"
    if not status["scan_ready"] and not status["rebuild_ready"]:
        return "verify-scan"
    if not status["clean_ready"] and not status["rebuild_ready"]:
        return "clean"
    if not status["preflight_ready"] and not status["rebuild_ready"]:
        return "preflight"
    if not status["rebuild_ready"]:
        return "rebuild"
    if not status["theme_done"]:
        return "theme"
    if not status["plugin_done"]:
        return "plugin"
    if status["plugin_manual"] and not status["manual_plugins_ack"]:
        return "manual-plugins"
    return "final"


STAGE_LABELS = {
    "backup-files": "Backup toàn bộ file website",
    "backup-db": "Backup database",
    "verify-scan": "Kiểm tra backup và quét mã độc",
    "clean": "Tạo bộ dữ liệu sạch",
    "preflight": "Kiểm tra an toàn trước rebuild",
    "rebuild": "Rebuild WordPress + database",
    "theme": "Khôi phục theme an toàn",
    "plugin": "Cài lại plugin sạch",
    "manual-plugins": "Xử lý plugin cần cài thủ công",
    "final": "Kiểm tra cuối website live",
    "done": "Hoàn tất",
}


def _new_run(paths: dict[str, Path], profile: SiteConnectionProfile) -> dict[str, Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    old_report_dir = REPORTS_DIR / profile.host
    if old_report_dir.is_dir():
        history = old_report_dir / "history" / timestamp
        history.mkdir(parents=True, exist_ok=True)
        for item in old_report_dir.glob("*.json"):
            try:
                shutil.copy2(item, history / item.name)
            except OSError:
                pass
    backup_root = BACKUPS_DIR / profile.host
    if backup_root.exists():
        backup_root = BACKUPS_DIR / f"{profile.host}-{timestamp}"
    paths = dict(paths)
    paths["backup"] = backup_root
    for key in ("preflight", "execute", "scan", "final"):
        paths[key].unlink(missing_ok=True)
    _write_json(
        paths["state"],
        {
            "host": profile.host,
            "profile": str(paths["profile"]),
            "backup_root": str(backup_root),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    return paths


def _stage_connection(profile: SiteConnectionProfile, transport: FTPTransport) -> None:
    console.print("\n[bold cyan]BƯỚC 3 — KIỂM TRA KẾT NỐI[/bold cyan]")
    with console.status("[cyan]Đang kết nối hosting...[/cyan]", spinner="dots"):
        cwd = transport.test_connection()
        root_ok = transport.directory_exists(profile.remote_path)
    if not root_ok:
        raise LoiBuoc(f"Không truy cập được thư mục WordPress đã cấu hình: {profile.remote_path}")
    console.print(f"[green]✓ Kết nối thành công.[/green] Thư mục FTP hiện tại: {cwd}")
    console.print(f"[green]✓ Thư mục WordPress hợp lệ:[/green] {profile.remote_path}")
    if not profile.use_tls:
        console.print("[yellow]⚠ Dự án đang dùng FTP thường, dữ liệu truyền không được mã hóa.[/yellow]")


def _stage_backup_files(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 4 — BACKUP FILE WEBSITE[/bold cyan]")
    backup_root = paths["backup"]
    console.print(f"Nơi lưu backup: [bold]{backup_root}[/bold]")
    with console.status("[cyan]Đang chuẩn bị backup...[/cyan]", spinner="dots") as status_ui:
        def progress(event: dict) -> None:
            phase = event.get("phase")
            stage = event.get("stage", "")
            if phase == "stage":
                status_ui.update(f"[cyan]Đang xử lý {stage}...[/cyan]")
            elif phase == "discover":
                status_ui.update(
                    f"[cyan]Đang quét {stage}: {event.get('files_found', 0)} file...[/cyan]"
                )
            elif phase == "transfer":
                status_ui.update(
                    f"[cyan]Đang tải {stage}: {event.get('files_completed', 0)}/{event.get('files_total', 0)} file[/cyan]"
                )
            elif phase == "retry":
                status_ui.update(
                    f"[yellow]FTP chập chờn, đang thử lại {event.get('attempt')}/{event.get('max_attempts')}...[/yellow]"
                )
            elif phase == "verify":
                status_ui.update("[cyan]Đang tạo và kiểm tra SHA-256 backup...[/cyan]")

        report = backup_wordpress_ftp(
            transport,
            profile.remote_path,
            backup_root,
            resume=True,
            progress=progress,
        )
    if not report.verified:
        raise LoiBuoc("Backup file chưa vượt qua kiểm tra toàn vẹn. Không được tiếp tục rebuild.")
    console.print(f"[green]✓ Backup file hoàn tất.[/green] Tổng file: {sum(item.files_total for item in report.items)}")
    if report.exclusions:
        console.print(f"[yellow]⚠ Có {len(report.exclusions)} file không đọc được và đã bị loại khỏi restore.[/yellow]")


def _stage_backup_db(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 5 — BACKUP DATABASE[/bold cyan]")
    target = paths["backup"] / "database" / "original.sql"
    with console.status("[cyan]Đang xuất database...[/cyan]", spinner="dots") as status_ui:
        def progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "upload_bridge":
                status_ui.update("[cyan]Đang tạo cầu nối database tạm thời...[/cyan]")
            elif phase == "request_dump":
                status_ui.update("[cyan]Đang yêu cầu hosting xuất database...[/cyan]")
            elif phase == "download":
                status_ui.update(f"[cyan]Đang tải database: {event.get('bytes_downloaded', 0)} byte[/cyan]")
            elif phase == "remove_bridge":
                status_ui.update("[cyan]Đang dọn cầu nối database tạm thời...[/cyan]")

        result = export_database_via_php_bridge(profile, transport, target, progress=progress)
    manifest = write_manifest(paths["backup"])
    ok, problems = verify_manifest(paths["backup"], manifest)
    if not ok:
        raise LoiBuoc("Backup database xong nhưng kiểm tra toàn bộ backup thất bại: " + "; ".join(problems))
    console.print(f"[green]✓ Database đã backup:[/green] {result.sql_path}")
    console.print(f"SHA-256: {result.sha256}")


def _stage_verify_scan(profile: SiteConnectionProfile, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 6 — KIỂM TRA BACKUP VÀ QUÉT MÃ ĐỘC[/bold cyan]")
    backup_root = paths["backup"]
    with console.status("[cyan]Đang xác minh manifest SHA-256...[/cyan]", spinner="dots"):
        ok, problems = verify_manifest(backup_root, backup_root / "manifest.json")
    if not ok:
        raise LoiBuoc("Backup không còn nguyên vẹn: " + "; ".join(problems))
    console.print("[green]✓ Backup nguyên vẹn.[/green]")

    sql_path = backup_root / "database" / "original.sql"
    uploads_path = backup_root / "uploads"
    with console.status("[cyan]Đang quét database và uploads...[/cyan]", spinner="dots"):
        db_findings = scan_sql(sql_path) if sql_path.is_file() else []
        upload_findings = scan_uploads(uploads_path) if uploads_path.is_dir() else []

    findings = [*db_findings, *upload_findings]
    blocking = [item for item in findings if getattr(item, "score", 0) >= 60]
    console.print(f"Phát hiện: {len(findings)} cảnh báo | HIGH/CRITICAL: {len(blocking)}")
    for item in blocking[:15]:
        console.print(f" - [yellow]{item.severity.value} {item.score}/100[/yellow] {item.location}")
    if blocking:
        console.print("[cyan]Các phát hiện vẫn được giữ trong backup gốc; bước clean sẽ loại dữ liệu không an toàn theo policy hiện tại.[/cyan]")
    _write_json(
        paths["scan"],
        {
            "host": profile.host,
            "database_findings": len(db_findings),
            "uploads_findings": len(upload_findings),
            "high_or_critical": len(blocking),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def _stage_clean(profile: SiteConnectionProfile, password: str, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 7 — TẠO BỘ DỮ LIỆU SẠCH[/bold cyan]")
    with console.status("[cyan]Đang tạo clean staging...[/cyan]", spinner="dots"):
        report = build_clean_restore(
            paths["backup"],
            ftp_password=password,
            host=profile.host,
        )
    console.print(f"[green]✓ Clean staging đã xác minh SHA-256.[/green]")
    console.print(f"Uploads giữ lại: {report.uploads_copied} | loại bỏ/cách ly: {report.uploads_dropped}")
    console.print(f"Database sạch: {report.database_clean}")
    console.print("Tài khoản quản trị mới: admin | mật khẩu: dùng cùng mật khẩu FTP theo cấu hình quy trình.")


def _stage_preflight(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 8 — KIỂM TRA AN TOÀN TRƯỚC REBUILD[/bold cyan]")
    with console.status("[cyan]Đang kiểm tra backup, clean staging và thư mục remote...[/cyan]", spinner="dots"):
        report = run_rebuild_preflight(
            host=profile.host,
            transport=transport,
            remote_root=profile.remote_path,
            backup_root=paths["backup"],
            report_path=paths["preflight"],
            fast=True,
        )
    if not report.ready_for_destructive_rebuild:
        raise LoiBuoc("Preflight chưa cho phép rebuild phá hủy.")
    console.print("[green]✓ Preflight PASS — đủ điều kiện rebuild.[/green]")


def _stage_rebuild(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> None:
    console.print("\n[bold red]BƯỚC 9 — REBUILD WORDPRESS + DATABASE[/bold red]")
    console.print("Website sẽ bị xóa code cũ trong đúng thư mục WordPress đã cấu hình, sau đó cài WordPress sạch và import database sạch.")
    if not Confirm.ask("Bạn xác nhận bắt đầu bước rebuild phá hủy?", default=False):
        raise TamDungQuyTrinh("Bạn chưa xác nhận rebuild. Có thể chạy BATDAU lại khi sẵn sàng.")

    with console.status("[cyan]Đang chuẩn bị rebuild...[/cyan]", spinner="dots") as status_ui:
        def progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "verify_original":
                status_ui.update("[cyan]Đang xác minh backup gốc...[/cyan]")
            elif phase == "verify_clean":
                status_ui.update("[cyan]Đang xác minh clean staging...[/cyan]")
            elif phase == "download_core":
                status_ui.update("[cyan]Đang tải WordPress sạch từ wordpress.org...[/cyan]")
            elif phase == "extract_core":
                status_ui.update("[cyan]Đang giải nén và kiểm tra WordPress...[/cyan]")
            elif phase == "destructive_boundary":
                status_ui.update("[red]Đã vượt qua điểm phá hủy — đang rebuild website...[/red]")
            elif phase == "wipe":
                status_ui.update(
                    f"[red]Đang xóa code cũ: {event.get('deleted_files', 0)} file, {event.get('deleted_dirs', 0)} thư mục[/red]"
                )
            elif phase.startswith("upload_"):
                status_ui.update(
                    f"[cyan]Đang upload {phase.removeprefix('upload_').replace('_', ' ')}: "
                    f"{event.get('files_completed', 0)}/{event.get('files_total', 0)}[/cyan]"
                )
            elif phase == "db_import_upload":
                status_ui.update("[cyan]Đang đưa database sạch lên staging tạm...[/cyan]")
            elif phase == "db_import_execute":
                status_ui.update("[cyan]Đang import database sạch...[/cyan]")
            elif phase == "db_import_cleanup":
                status_ui.update("[cyan]Đang dọn file import tạm...[/cyan]")

        report = rebuild_engine.execute_rebuild(
            profile=profile,
            transport=transport,
            backup_root=paths["backup"],
            preflight_path=paths["preflight"],
            report_path=paths["execute"],
            restore_backup_code=False,
            progress=progress,
        )
    if not report.database_imported:
        raise LoiBuoc("WordPress đã rebuild nhưng database chưa import thành công. Vui lòng báo kỹ thuật.")
    console.print(f"[green]✓ WordPress {report.wordpress_version} + database đã rebuild thành công.[/green]")
    console.print("[green]✓ Không restore plugin/theme code cũ từ website nhiễm.[/green]")


def _stage_theme(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> None:
    console.print("\n[bold cyan]BƯỚC 10 — THEME[/bold cyan]")
    result = _run_theme_stage(
        profile=profile,
        transport=transport,
        backup_root=paths["backup"],
        report_path=paths["execute"],
    )
    if result.child_theme_detected and not result.child_installed:
        if result.child_repair_workspace:
            console.print("\n[bold yellow]Theme con đang chờ kỹ thuật sửa.[/bold yellow]")
            console.print(f"Thư mục cần sửa: [bold]{result.child_repair_workspace}[/bold]")
            console.print("Backup gốc vẫn giữ nguyên, không sửa trực tiếp trong backups/.")
            raise TamDungQuyTrinh(
                "Sau khi kỹ thuật sửa xong working-copy, chạy BATDAU.bat lại. Hệ thống sẽ tự tiếp tục từ bước theme."
            )
        raise TamDungQuyTrinh(
            "Theme con chưa được cài. Chạy BATDAU lại và xác nhận cài theme con trước khi tiếp tục."
        )
    if result.unsupported_theme or result.mode == "detection-unavailable":
        _save_operator_state(paths, profile, manual_theme_ack=False)
        console.print("[yellow]Theme này không hỗ trợ cài tự động. Vui lòng cài theme sạch thủ công.[/yellow]")
        if Confirm.ask("Bạn đã cài theme thủ công và muốn tiếp tục plugin?", default=False):
            _save_operator_state(paths, profile, manual_theme_ack=True)
            return
        raise TamDungQuyTrinh("Đã tạm dừng để cài theme thủ công. Chạy BATDAU lại sau khi hoàn tất.")
    console.print("[green]✓ Theme stage hoàn tất.[/green]")


def _stage_plugin(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    paths: dict[str, Path],
    *,
    selected_slugs=None,
    progress=None,
) -> None:
    console.print("\n[bold cyan]BƯỚC 11 — PLUGIN[/bold cyan]")
    result = run_plugin_stage(
        profile=profile,
        transport=transport,
        backup_root=paths["backup"],
        report_path=paths["execute"],
        selected_slugs=selected_slugs,
        progress=progress,
    )
    if result.lookup_error_count:
        raise TamDungQuyTrinh(
            f"Có {result.lookup_error_count} plugin chưa xác minh được trên WordPress.org. Chạy BATDAU lại để thử plugin stage sau."
        )
    install_target = int(result.install_target_count or 0)
    if install_target and result.installed_count < install_target:
        raise TamDungQuyTrinh(
            "Một số plugin WordPress.org chưa được cài. Chạy BATDAU lại để hoàn tất plugin stage."
        )
    console.print("[green]✓ Plugin public đã được xử lý từ nguồn sạch WordPress.org.[/green]")


def _stage_manual_plugins(profile: SiteConnectionProfile, paths: dict[str, Path], status: dict[str, Any]) -> None:
    count = int(status.get("plugin_manual") or 0)
    console.print("\n[bold yellow]BƯỚC 12 — PLUGIN CẦN CÀI THỦ CÔNG[/bold yellow]")
    console.print(f"Có [bold]{count}[/bold] plugin không có trên WordPress.org.")
    console.print("Vui lòng lấy bản sạch từ nhà cung cấp rồi upload thủ công. Không copy plugin code cũ từ backup.")
    choice = Prompt.ask(
        "Chọn hành động",
        choices=["1", "2", "3"],
        default="1",
    )
    console.print("1 = Tôi đã upload xong | 2 = Tôi chấp nhận tiếp tục và xử lý sau | 3 = Tạm dừng")
    # Prompt is intentionally repeated after the legend so operators see the choices clearly.
    choice = Prompt.ask("Lựa chọn", choices=["1", "2", "3"], default=choice)
    if choice == "3":
        raise TamDungQuyTrinh("Đã tạm dừng để xử lý plugin thủ công. Chạy BATDAU lại sau khi hoàn tất.")
    _save_operator_state(
        paths,
        profile,
        manual_plugins_ack=True,
        manual_plugins_note="uploaded" if choice == "1" else "accepted-for-later",
    )


ISSUE_VI = {
    "core-missing": "Thiếu file WordPress core",
    "core-unreadable": "Không đọc được file WordPress core",
    "core-checksum": "Checksum WordPress core không khớp",
    "uploads-executable": "Có file thực thi trong uploads",
    "temporary-artifact": "Còn file tạm của công cụ",
    "unexpected-root-code": "Có file thực thi lạ ở thư mục gốc",
    "known-malware-marker": "Phát hiện dấu hiệu mã độc đã biết",
    "scan-unreadable": "Không đọc được file khi quét runtime",
    "http-home": "Trang chủ không truy cập bình thường",
    "http-admin": "Trang quản trị không truy cập bình thường",
}


def _stage_final(profile: SiteConnectionProfile, transport: FTPTransport, paths: dict[str, Path]) -> str:
    console.print("\n[bold cyan]BƯỚC 13 — KIỂM TRA CUỐI WEBSITE LIVE[/bold cyan]")
    with console.status("[cyan]Đang kiểm tra toàn bộ website sau rebuild...[/cyan]", spinner="dots") as status_ui:
        def progress(event: dict) -> None:
            phase = event.get("phase")
            if phase == "inventory":
                status_ui.update("[cyan]Đang kiểm kê file live qua FTP...[/cyan]")
            elif phase == "core_checksums":
                status_ui.update(f"[cyan]Đang lấy checksum WordPress {event.get('version', '')}...[/cyan]")
            elif phase == "core_hash":
                status_ui.update(
                    f"[cyan]Đang đối chiếu WordPress core: {event.get('completed', 0)}/{event.get('total', 0)}[/cyan]"
                )
            elif phase == "malware_scan_start":
                status_ui.update(f"[cyan]Đang chuẩn bị quét {event.get('total', 0)} file runtime...[/cyan]")
            elif phase == "malware_scan":
                status_ui.update(
                    f"[cyan]Đang quét runtime: {event.get('completed', 0)}/{event.get('total', 0)}[/cyan]"
                )
            elif phase == "http":
                status_ui.update("[cyan]Đang kiểm tra trang chủ và wp-admin...[/cyan]")

        result = verify_live_site(
            profile=profile,
            transport=transport,
            report_path=paths["execute"],
            progress=progress,
        )
    save_live_verify_report(result, paths["final"])

    console.print("\n[bold]KẾT QUẢ KIỂM TRA CUỐI[/bold]")
    console.print(f"File live đã kiểm kê: {result.remote_files}")
    console.print(f"WordPress: {result.wordpress_version or 'không xác định'}")
    console.print(
        f"Core: đúng={result.core_verified}/{result.core_expected} | thiếu={result.core_missing} | "
        f"sai checksum={result.core_mismatched} | không đọc được={result.core_unreadable}"
    )
    console.print(f"Dấu hiệu mã độc đã biết: {result.suspicious_markers}")
    console.print(f"File thực thi trong uploads: {result.uploads_executables}")
    console.print(f"File tạm còn sót: {result.temp_artifacts}")
    if result.http_home:
        console.print(f"Trang chủ: {'PASS' if result.http_home.ok else 'FAIL'} | HTTP {result.http_home.status}")
    if result.http_admin:
        console.print(f"wp-admin: {'PASS' if result.http_admin.ok else 'FAIL'} | HTTP {result.http_admin.status}")

    if result.issues:
        console.print("\n[bold red]Các mục cần xử lý:[/bold red]")
        for issue in result.issues[:30]:
            console.print(f" - [red]{ISSUE_VI.get(issue.category, issue.category)}[/red] — {issue.path}: {issue.detail}")
    if result.warnings:
        console.print("\n[bold yellow]Cảnh báo:[/bold yellow]")
        for warning in result.warnings:
            console.print(f" - {warning}")

    if result.status == "PASS":
        console.print("\n[bold green]✅ HOÀN TẤT — WEBSITE VƯỢT QUA KIỂM TRA CUỐI.[/bold green]")
    elif result.status == "PASS WITH WARNINGS":
        console.print("\n[bold yellow]⚠ HOÀN TẤT CÓ CẢNH BÁO — website hoạt động, còn mục chưa xác minh đầy đủ.[/bold yellow]")
    else:
        console.print("\n[bold red]❌ CHƯA THỂ BÀN GIAO — kiểm tra cuối còn mục cần kỹ thuật xử lý.[/bold red]")
    return result.status


def _prepare_project() -> tuple[Path, SiteConnectionProfile, str, FTPTransport, dict[str, Path]]:
    while True:
        profile_path = _select_project()
        try:
            profile = load_site_profile(profile_path)
        except Exception as exc:
            console.print(f"[red]Cấu hình dự án không hợp lệ:[/red] {exc}")
            if Confirm.ask("Sửa cấu hình dự án ngay?", default=True):
                _edit_project(profile_path)
                continue
            raise LoiBuoc("Không thể tiếp tục với cấu hình hiện tại.") from exc

        password = _runtime_password(profile)
        transport = _transport(profile, password)
        try:
            _stage_connection(profile, transport)
        except Exception as exc:
            console.print(f"[red]Không kết nối được hosting:[/red] {exc}")
            if Confirm.ask("Bạn có muốn sửa lại thông tin FTP?", default=True):
                _edit_project(profile_path)
                continue
            raise
        paths = _paths(profile, profile_path)
        return profile_path, profile, password, transport, paths


def _choose_start(profile: SiteConnectionProfile, paths: dict[str, Path]) -> dict[str, Path]:
    status = _infer_status(paths)
    _show_status(profile, paths, status)
    next_stage = _next_stage(status)

    if next_stage == "done":
        console.print("\n[green]Dự án này đã có kết quả kiểm tra cuối đạt yêu cầu.[/green]")
        choice = Prompt.ask(
            "Bạn muốn làm gì? 1=Kiểm tra cuối lại, 2=Bắt đầu phiên xử lý mới, 3=Thoát",
            choices=["1", "2", "3"],
            default="3",
        )
        if choice == "1":
            paths["final"].unlink(missing_ok=True)
            return paths
        if choice == "2":
            return _new_run(paths, profile)
        raise TamDungQuyTrinh("Đã thoát theo lựa chọn của người dùng.")

    console.print(f"\nBước hệ thống đề xuất tiếp theo: [bold cyan]{STAGE_LABELS[next_stage]}[/bold cyan]")
    if Confirm.ask("Tiếp tục tự động từ bước này?", default=True):
        return paths

    choice = Prompt.ask(
        "Chọn 1=Tiếp tục bước đề xuất, 2=Chạy kiểm tra cuối, 3=Bắt đầu phiên xử lý mới, 4=Thoát",
        choices=["1", "2", "3", "4"],
        default="1",
    )
    if choice == "1":
        return paths
    if choice == "2":
        # Mark earlier stages untouched; main will directly run final once.
        _save_operator_state(paths, profile, force_final_once=True)
        return paths
    if choice == "3":
        return _new_run(paths, profile)
    raise TamDungQuyTrinh("Đã thoát theo lựa chọn của người dùng.")


def run() -> int:
    console.print(
        Panel.fit(
            "[bold cyan]WP CLEAN REBUILD — TRÌNH ĐIỀU KHIỂN NHÂN SỰ[/bold cyan]\n"
            "Làm theo hướng dẫn trên màn hình. Không cần nhớ câu lệnh kỹ thuật.",
            border_style="cyan",
        )
    )

    try:
        _profile_path, profile, password, transport, paths = _prepare_project()
        paths = _choose_start(profile, paths)
        _save_operator_state(paths, profile)

        while True:
            operator_state = _read_json(paths["state"])
            if operator_state.pop("force_final_once", False):
                _write_json(paths["state"], operator_state)
                final_status = _stage_final(profile, transport, paths)
                return 0 if final_status in {"PASS", "PASS WITH WARNINGS"} else 2

            status = _infer_status(paths)
            stage = _next_stage(status)
            if stage == "done":
                console.print("\n[bold green]✅ QUY TRÌNH ĐÃ HOÀN TẤT.[/bold green]")
                console.print(f"Báo cáo cuối: {paths['final']}")
                return 0

            console.print(f"\n[bold]Tiếp theo:[/bold] {STAGE_LABELS[stage]}")
            if stage == "backup-files":
                _stage_backup_files(profile, transport, paths)
            elif stage == "backup-db":
                _stage_backup_db(profile, transport, paths)
            elif stage == "verify-scan":
                _stage_verify_scan(profile, paths)
            elif stage == "clean":
                _stage_clean(profile, password, paths)
            elif stage == "preflight":
                _stage_preflight(profile, transport, paths)
            elif stage == "rebuild":
                _stage_rebuild(profile, transport, paths)
            elif stage == "theme":
                _stage_theme(profile, transport, paths)
            elif stage == "plugin":
                _stage_plugin(profile, transport, paths)
            elif stage == "manual-plugins":
                _stage_manual_plugins(profile, paths, status)
            elif stage == "final":
                final_status = _stage_final(profile, transport, paths)
                if final_status == "BLOCKED":
                    return 2
            else:
                raise LoiBuoc(f"Bước không xác định: {stage}")

            _save_operator_state(paths, profile, last_completed_stage=stage)

    except TamDungQuyTrinh as exc:
        console.print(f"\n[bold yellow]TẠM DỪNG:[/bold yellow] {exc}")
        console.print("Khi sẵn sàng, chỉ cần chạy lại [bold].\\BATDAU.bat[/bold].")
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Đã dừng theo yêu cầu. Chạy BATDAU.bat lại để tiếp tục từ trạng thái hiện có.[/yellow]")
        return 130
    except Exception as exc:
        console.print(f"\n[bold red]QUY TRÌNH DỪNG DO LỖI:[/bold red] {type(exc).__name__}: {exc}")
        console.print("[yellow]Không chạy lại các bước phá hủy bằng tay. Hãy chạy BATDAU.bat lại hoặc gửi lỗi cho kỹ thuật.[/yellow]")
        return 2


if __name__ == "__main__":
    sys.exit(run())
