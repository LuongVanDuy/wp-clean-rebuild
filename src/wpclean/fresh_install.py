from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import base64
import json
import re
import secrets

from .project_journal import append_activity
from .rebuild_execute import (
    _delete_remote_file,
    _download_wordpress_package,
    _ensure_remote_dir,
    _extract_wordpress,
    _php_single_quote,
    _sha256_bytes,
    _upload_text,
    _upload_tree,
    _wipe_remote_root,
    build_clean_htaccess,
    SALT_KEYS,
)
from .site_config import SiteConnectionProfile


ProgressCallback = Callable[[dict[str, Any]], None]
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(slots=True)
class FreshInstallRequest:
    project_name: str
    site_url: str
    site_title: str
    admin_user: str
    admin_password: str
    admin_email: str
    table_prefix: str
    ftp_host: str
    ftp_username: str
    ftp_password: str
    ftp_protocol: str
    ftp_port: int
    remote_path: str
    passive: bool = True
    workers: int = 6
    block_mb: int = 1
    db_mode: str = "existing"  # existing | create
    db_host: str = "localhost"
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    mysql_admin_host: str = "localhost"
    mysql_admin_user: str = ""
    mysql_admin_password: str = ""
    mysql_user_host: str = "localhost"
    wipe_existing: bool = False
    confirm_text: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FreshInstallRequest":
        host = str(data.get("ftpHost") or "").strip()
        protocol = str(data.get("ftpProtocol") or "ftp").strip().lower()
        site_url = str(data.get("siteUrl") or (f"https://{host}" if host else "")).strip().rstrip("/")
        obj = cls(
            project_name=str(data.get("projectName") or host).strip(),
            site_url=site_url,
            site_title=str(data.get("siteTitle") or "Website mới").strip(),
            admin_user=str(data.get("adminUser") or "admin").strip(),
            admin_password=str(data.get("adminPassword") or ""),
            admin_email=str(data.get("adminEmail") or "").strip(),
            table_prefix=str(data.get("tablePrefix") or "wp_").strip(),
            ftp_host=host,
            ftp_username=str(data.get("ftpUsername") or "").strip(),
            ftp_password=str(data.get("ftpPassword") or ""),
            ftp_protocol=protocol,
            ftp_port=int(data.get("ftpPort") or 21),
            remote_path=str(data.get("remotePath") or (f"/domains/{host}/public_html" if host else "")).strip(),
            passive=bool(data.get("passive", True)),
            workers=max(1, min(16, int(data.get("workers") or 6))),
            block_mb=max(1, min(8, int(data.get("blockMb") or 1))),
            db_mode=str(data.get("dbMode") or "existing").strip().lower(),
            db_host=str(data.get("dbHost") or "localhost").strip(),
            db_name=str(data.get("dbName") or "").strip(),
            db_user=str(data.get("dbUser") or "").strip(),
            db_password=str(data.get("dbPassword") or ""),
            mysql_admin_host=str(data.get("mysqlAdminHost") or data.get("dbHost") or "localhost").strip(),
            mysql_admin_user=str(data.get("mysqlAdminUser") or "").strip(),
            mysql_admin_password=str(data.get("mysqlAdminPassword") or ""),
            mysql_user_host=str(data.get("mysqlUserHost") or "localhost").strip(),
            wipe_existing=bool(data.get("wipeExisting")),
            confirm_text=str(data.get("confirmText") or "").strip(),
        )
        obj.validate()
        return obj

    @property
    def site_host(self) -> str:
        return (urlsplit(self.site_url).hostname or self.ftp_host).strip().lower()

    @property
    def profile(self) -> SiteConnectionProfile:
        return SiteConnectionProfile(
            host=self.ftp_host,
            username=self.ftp_username,
            password=self.ftp_password,
            protocol=self.ftp_protocol,
            port=self.ftp_port,
            remote_path=self.remote_path,
            passive=self.passive,
            workers=self.workers,
            block_mb=self.block_mb,
            site_url=self.site_url,
        )

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(x for x in (self.ftp_password, self.db_password, self.mysql_admin_password, self.admin_password) if x)

    def validate(self) -> None:
        if not self.ftp_host or not self.ftp_username or not self.ftp_password:
            raise ValueError("FTP host, tài khoản và mật khẩu là bắt buộc.")
        if self.ftp_protocol not in {"ftp", "ftps", "ftp+tls", "ftp-tls"}:
            raise ValueError("Chỉ hỗ trợ FTP hoặc FTPS.")
        if not self.site_url.lower().startswith(("http://", "https://")):
            raise ValueError("Website URL phải bắt đầu bằng http:// hoặc https://.")
        if not self.remote_path:
            raise ValueError("Remote WordPress path không được để trống.")
        if not self.admin_user or not self.admin_password or not self.admin_email:
            raise ValueError("Tài khoản, mật khẩu và email WordPress admin là bắt buộc.")
        if "@" not in self.admin_email:
            raise ValueError("Email WordPress admin không hợp lệ.")
        if not PREFIX_RE.fullmatch(self.table_prefix):
            raise ValueError("Table prefix chỉ được chứa chữ, số và dấu gạch dưới.")
        if self.db_mode not in {"existing", "create"}:
            raise ValueError("Chế độ database không hợp lệ.")
        if not self.db_name or not self.db_user or not self.db_password or not self.db_host:
            raise ValueError("DB host, database name, database user và password là bắt buộc.")
        if self.db_mode == "create":
            if not IDENTIFIER_RE.fullmatch(self.db_name) or not IDENTIFIER_RE.fullmatch(self.db_user):
                raise ValueError("Khi tự tạo DB, database name/user chỉ được chứa chữ, số và dấu gạch dưới.")
            if not self.mysql_admin_user or not self.mysql_admin_password:
                raise ValueError("Cần tài khoản MySQL có quyền tạo database/user.")
        if self.wipe_existing and self.confirm_text.lower() != self.site_host:
            raise ValueError(f"Muốn xóa thư mục đích phải nhập chính xác domain: {self.site_host}")


@dataclass(slots=True)
class FreshInstallReport:
    project_name: str
    host: str
    site_url: str
    remote_path: str
    db_mode: str
    db_name: str
    db_user: str
    started_at: str
    finished_at: str = ""
    wordpress_version: str = ""
    wordpress_package_sha256: str = ""
    files_uploaded: int = 0
    wiped_files: int = 0
    wiped_dirs: int = 0
    database_created: bool = False
    wordpress_installed: bool = False
    bridge_removed: bool = False
    completed: bool = False

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_wp_config(req: FreshInstallRequest) -> str:
    lines = [
        "<?php",
        "/** Generated by WP Clean Rebuild fresh installer. */",
        f"define('DB_NAME', '{_php_single_quote(req.db_name)}');",
        f"define('DB_USER', '{_php_single_quote(req.db_user)}');",
        f"define('DB_PASSWORD', '{_php_single_quote(req.db_password)}');",
        f"define('DB_HOST', '{_php_single_quote(req.db_host)}');",
        "define('DB_CHARSET', 'utf8mb4');",
        "define('DB_COLLATE', '');",
        "",
    ]
    for key in SALT_KEYS:
        lines.append(f"define('{key}', '{_php_single_quote(secrets.token_urlsafe(64))}');")
    lines.extend([
        "",
        f"$table_prefix = '{_php_single_quote(req.table_prefix)}';",
        "define('WP_DEBUG', false);",
        "define('DISALLOW_FILE_EDIT', true);",
        "if ( ! defined('ABSPATH') ) { define('ABSPATH', __DIR__ . '/'); }",
        "require_once ABSPATH . 'wp-settings.php';",
        "",
    ])
    return "\n".join(lines)


def _encoded_config(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _database_create_bridge(req: FreshInstallRequest, token: str) -> str:
    cfg = _encoded_config({
        "admin_host": req.mysql_admin_host,
        "admin_user": req.mysql_admin_user,
        "admin_pass": req.mysql_admin_password,
        "db_name": req.db_name,
        "db_user": req.db_user,
        "db_pass": req.db_password,
        "user_host": req.mysql_user_host,
    })
    return r'''<?php
@set_time_limit(60); @ini_set('display_errors','0');
header('Content-Type: application/json; charset=utf-8'); header('Cache-Control: no-store');
$token='__TOKEN__'; $provided=$_SERVER['HTTP_X_WPCLEAN_TOKEN'] ?? '';
function out($ok,$message,$extra=array(),$code=200){http_response_code($code);echo json_encode(array_merge(array('ok'=>$ok,'message'=>$message),$extra));exit;}
if(!$provided || !hash_equals($token,(string)$provided)){out(false,'unauthorized',array(),403);}
$cfg=json_decode(base64_decode('__CFG__'),true); if(!is_array($cfg)){out(false,'invalid config',array(),500);}
mysqli_report(MYSQLI_REPORT_OFF); $db=@new mysqli($cfg['admin_host'],$cfg['admin_user'],$cfg['admin_pass']);
if($db->connect_errno){out(false,'MySQL admin login failed: '.$db->connect_error,array(),500);}
$name=$cfg['db_name']; $user=$cfg['db_user']; $pass=$db->real_escape_string($cfg['db_pass']); $host=$db->real_escape_string($cfg['user_host']);
if(!preg_match('/^[A-Za-z0-9_]+$/',$name)||!preg_match('/^[A-Za-z0-9_]+$/',$user)){out(false,'unsafe database identifier',array(),400);}
if(!$db->query("CREATE DATABASE IF NOT EXISTS `{$name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")){out(false,'CREATE DATABASE failed: '.$db->error,array(),500);}
if($cfg['admin_user'] !== $user){
  if(!$db->query("CREATE USER IF NOT EXISTS '{$user}'@'{$host}' IDENTIFIED BY '{$pass}'")){out(false,'CREATE USER failed: '.$db->error,array(),500);}
  @$db->query("ALTER USER '{$user}'@'{$host}' IDENTIFIED BY '{$pass}'");
  if(!$db->query("GRANT ALL PRIVILEGES ON `{$name}`.* TO '{$user}'@'{$host}'")){out(false,'GRANT failed: '.$db->error,array(),500);}
}
out(true,'database ready',array('database'=>$name,'user'=>$user));
'''.replace("__TOKEN__", _php_single_quote(token)).replace("__CFG__", cfg)


def _wordpress_install_bridge(req: FreshInstallRequest, token: str) -> str:
    cfg = _encoded_config({
        "title": req.site_title,
        "admin_user": req.admin_user,
        "admin_pass": req.admin_password,
        "admin_email": req.admin_email,
    })
    return r'''<?php
@set_time_limit(120); @ini_set('display_errors','0');
header('Content-Type: application/json; charset=utf-8'); header('Cache-Control: no-store');
$token='__TOKEN__'; $provided=$_SERVER['HTTP_X_WPCLEAN_TOKEN'] ?? '';
function out($ok,$message,$extra=array(),$code=200){http_response_code($code);echo json_encode(array_merge(array('ok'=>$ok,'message'=>$message),$extra));exit;}
if(!$provided || !hash_equals($token,(string)$provided)){out(false,'unauthorized',array(),403);}
$cfg=json_decode(base64_decode('__CFG__'),true); if(!is_array($cfg)){out(false,'invalid config',array(),500);}
define('WP_INSTALLING', true); require_once __DIR__.'/wp-load.php'; require_once ABSPATH.'wp-admin/includes/upgrade.php';
if(is_blog_installed()){out(false,'WordPress is already installed in this database',array(),409);}
$result=wp_install($cfg['title'],$cfg['admin_user'],$cfg['admin_email'],true,'',$cfg['admin_pass']);
if(is_wp_error($result)){out(false,$result->get_error_message(),array(),500);}
out(true,'wordpress installed',array('user_id'=>intval($result['user_id'] ?? 0)));
'''.replace("__TOKEN__", _php_single_quote(token)).replace("__CFG__", cfg)


def _call_bridge(site_url: str, filename: str, token: str) -> dict[str, Any]:
    url = site_url.rstrip("/") + "/" + filename
    request = Request(url, data=b"{}", headers={"X-WPClean-Token": token, "Content-Type": "application/json", "User-Agent": "WP-Clean-Rebuild/1.0"}, method="POST")
    with urlopen(request, timeout=180) as response:
        body = response.read(1024 * 1024)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("PHP installer không trả về JSON hợp lệ.") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(str(payload.get("message") if isinstance(payload, dict) else "Installer failed"))
    return payload


def _remote_entries(transport, remote_path: str) -> list[str]:
    client = transport._new_client()
    try:
        try:
            entries = list(transport._mlsd(client, remote_path))
        except Exception:
            _ensure_remote_dir(client, remote_path)
            entries = list(transport._mlsd(client, remote_path))
        return [name for name, _facts in entries if name not in {".", "..", ".well-known"}]
    finally:
        try: client.quit()
        except Exception: client.close()


def run_fresh_install(
    req: FreshInstallRequest,
    *,
    transport,
    report_dir: Path,
    progress: ProgressCallback | None = None,
) -> FreshInstallReport:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    report = FreshInstallReport(req.project_name, req.ftp_host, req.site_url, req.remote_path, req.db_mode, req.db_name, req.db_user, started)

    def emit(phase: str, message: str, **extra: Any) -> None:
        append_activity(report_dir, project=req.project_name, stage="fresh-install", message=message, secrets=req.secrets)
        if progress:
            progress({"phase": phase, "message": message, **extra})

    emit("ftp", "Kiểm tra FTP và thư mục đích")
    transport.test_connection()
    entries = _remote_entries(transport, req.remote_path)
    if entries and not req.wipe_existing:
        raise RuntimeError("Thư mục đích không rỗng. Bật 'Xóa dữ liệu hiện có' và xác nhận domain nếu muốn cài mới hoàn toàn.")
    if entries and req.wipe_existing:
        emit("wipe", "Đang xóa dữ liệu cũ trong thư mục đích")
        report.wiped_files, report.wiped_dirs, _ = _wipe_remote_root(transport, req.remote_path, progress=progress)

    if req.db_mode == "create":
        emit("database", "Đang tạo database và database user mới")
        token = secrets.token_urlsafe(32)
        filename = f".wpclean-db-{secrets.token_hex(8)}.php"
        remote = str(PurePosixPath(req.remote_path) / filename)
        _upload_text(transport, remote, _database_create_bridge(req, token))
        try:
            _call_bridge(req.site_url, filename, token)
            report.database_created = True
        finally:
            _delete_remote_file(transport, remote)
    else:
        emit("database", "Sử dụng database có sẵn theo cấu hình")

    emit("download", "Đang tải WordPress sạch từ wordpress.org")
    package = _download_wordpress_package(progress=progress)
    report.wordpress_package_sha256 = _sha256_bytes(package)
    with TemporaryDirectory(prefix="wpclean-fresh-") as temp:
        root = Path(temp) / "wordpress"
        root.mkdir(parents=True, exist_ok=True)
        report.wordpress_version = _extract_wordpress(package, root)
        # Do not upload default bundled plugins/themes from wp-content. A fresh install
        # still gets WordPress core and the standard directories from the package.
        emit("upload", f"Đang upload WordPress {report.wordpress_version}")
        report.files_uploaded = _upload_tree(transport, root, req.remote_path, progress_phase="fresh_upload", progress=progress)

    emit("config", "Đang tạo wp-config.php và .htaccess sạch")
    _upload_text(transport, str(PurePosixPath(req.remote_path) / "wp-config.php"), build_wp_config(req))
    _upload_text(transport, str(PurePosixPath(req.remote_path) / ".htaccess"), build_clean_htaccess(req.site_url))

    emit("install", "Đang tạo bảng WordPress và tài khoản quản trị")
    token = secrets.token_urlsafe(32)
    filename = f".wpclean-install-{secrets.token_hex(8)}.php"
    remote = str(PurePosixPath(req.remote_path) / filename)
    _upload_text(transport, remote, _wordpress_install_bridge(req, token))
    try:
        _call_bridge(req.site_url, filename, token)
        report.wordpress_installed = True
    finally:
        report.bridge_removed = _delete_remote_file(transport, remote)

    report.completed = report.wordpress_installed and report.bridge_removed
    report.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
    emit("done", f"Cài WordPress {report.wordpress_version} hoàn tất")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "fresh-install.json").write_text(json.dumps(report.public_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report


__all__ = ["FreshInstallRequest", "FreshInstallReport", "build_wp_config", "run_fresh_install"]
