from __future__ import annotations

from pathlib import PurePosixPath
import secrets
import traceback

from . import fresh_install as fresh_module
from . import gui_fresh_entry as base
from . import gui_server as server
from .fresh_install import FreshInstallRequest, _call_bridge, _database_create_bridge, _encoded_config
from .rebuild_execute import _delete_remote_file, _ensure_remote_dir, _php_single_quote, _upload_text


_ORIGINAL_RUN_FRESH_INSTALL = base.run_fresh_install


def _database_test_bridge(req: FreshInstallRequest, token: str) -> str:
    cfg = _encoded_config(
        {
            "host": req.db_host,
            "name": req.db_name,
            "user": req.db_user,
            "pass": req.db_password,
        }
    )
    return r'''<?php
@set_time_limit(30); @ini_set('display_errors','0');
header('Content-Type: application/json; charset=utf-8'); header('Cache-Control: no-store');
$token='__TOKEN__'; $provided=$_SERVER['HTTP_X_WPCLEAN_TOKEN'] ?? '';
function out($ok,$message,$extra=array(),$code=200){http_response_code($code);echo json_encode(array_merge(array('ok'=>$ok,'message'=>$message),$extra));exit;}
if(!$provided || !hash_equals($token,(string)$provided)){out(false,'unauthorized',array(),403);}
$cfg=json_decode(base64_decode('__CFG__'),true); if(!is_array($cfg)){out(false,'invalid config',array(),500);}
mysqli_report(MYSQLI_REPORT_OFF); $db=@new mysqli($cfg['host'],$cfg['user'],$cfg['pass'],$cfg['name']);
if($db->connect_errno){out(false,'Database login failed: '.$db->connect_error,array(),500);}
if(!$db->query('SELECT 1')){out(false,'Database query failed: '.$db->error,array(),500);}
$result=$db->query('SHOW TABLES'); if(!$result){out(false,'Could not inspect database tables: '.$db->error,array(),500);}
$count=$result->num_rows; if($count>0){out(false,'Database is not empty. Fresh Install requires a new/empty database.',array('tables'=>$count),409);}
out(true,'database connection ready and empty',array('database'=>$cfg['name'],'tables'=>0));
'''.replace("__TOKEN__", _php_single_quote(token)).replace("__CFG__", cfg)


def _ensure_root(transport, remote_path: str) -> None:
    client = transport._new_client()
    try:
        _ensure_remote_dir(client, remote_path)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def _upload_and_call(transport, req: FreshInstallRequest, php_factory) -> None:
    token = secrets.token_urlsafe(32)
    filename = f"wpclean-preflight-{secrets.token_hex(8)}.php"
    remote = str(PurePosixPath(req.remote_path) / filename)
    _upload_text(transport, remote, php_factory(token))
    try:
        _call_bridge(req.site_url, filename, token)
    finally:
        _delete_remote_file(transport, remote)


def _preflight_database(req: FreshInstallRequest, transport, job) -> None:
    _ensure_root(transport, req.remote_path)
    if req.db_mode == "create":
        job.log("Preflight: kiểm tra quyền MySQL và tạo database/user trước destructive boundary", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_create_bridge(req, token))
        job.log("Preflight: xác minh database user mới đăng nhập được và database đang rỗng", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_test_bridge(req, token))
    else:
        job.log("Preflight: kiểm tra database có sẵn trước destructive boundary", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_test_bridge(req, token))
    job.log("Preflight database PASS · kết nối được và database rỗng", req.secrets)


def _visible_bridge_name(value: str) -> str:
    """Avoid dot-prefixed installer files because many hosts block HTTP access to dotfiles."""
    text = str(value)
    name = PurePosixPath(text).name
    if name.startswith(".wpclean-db-") or name.startswith(".wpclean-install-"):
        parent = str(PurePosixPath(text).parent)
        visible = name[1:]
        return visible if parent in {"", "."} else str(PurePosixPath(parent) / visible)
    return text


def _run_fresh_install_compatible(*args, **kwargs):
    original_upload = fresh_module._upload_text
    original_delete = fresh_module._delete_remote_file
    original_call = fresh_module._call_bridge

    def upload_visible(transport, remote_path: str, content: str) -> None:
        original_upload(transport, _visible_bridge_name(remote_path), content)

    def delete_visible(transport, remote_path: str) -> bool:
        return original_delete(transport, _visible_bridge_name(remote_path))

    def call_visible(site_url: str, filename: str, token: str):
        return original_call(site_url, _visible_bridge_name(filename), token)

    fresh_module._upload_text = upload_visible
    fresh_module._delete_remote_file = delete_visible
    fresh_module._call_bridge = call_visible
    try:
        return _ORIGINAL_RUN_FRESH_INSTALL(*args, **kwargs)
    finally:
        fresh_module._upload_text = original_upload
        fresh_module._delete_remote_file = original_delete
        fresh_module._call_bridge = original_call


def _safe_fresh_worker(job, req: FreshInstallRequest) -> None:
    report_dir = server.REPORTS_DIR / req.site_host
    try:
        transport = server.wizard._transport(req.profile, req.ftp_password)
        job.message = "Đang preflight FTP / database trước khi thay đổi hosting"
        job.percent = 2
        transport.test_connection()
        _preflight_database(req, transport, job)

        job.log("Bắt đầu cài WordPress mới", req.secrets)
        report = _run_fresh_install_compatible(
            req,
            transport=transport,
            report_dir=report_dir,
            progress=lambda event: base._fresh_progress(job, req, event),
        )
        job.report = report.public_dict()
        job.status = "success"
        job.percent = 100
        job.message = "WordPress mới đã được cài đặt thành công."
        job.log("Hoàn tất Fresh Install", req.secrets)
    except Exception as exc:
        destructive_started = any(
            marker in "\n".join(job.logs).lower()
            for marker in ("đang xóa dữ liệu cũ", "đang upload wordpress", "đang tạo wp-config")
        )
        job.status = "error"
        prefix = "Fresh Install" if destructive_started else "Preflight"
        job.error = f"{prefix} {type(exc).__name__}: {exc}"
        job.message = (
            "Cài đặt đã dừng sau khi bắt đầu thay đổi hosting; kiểm tra log trước khi chạy lại."
            if destructive_started
            else "Dừng trước destructive boundary; hosting chưa bị wipe bởi Fresh Install."
        )
        job.log(job.error, req.secrets)
        job.log(traceback.format_exc(limit=4), req.secrets)


# Keep Fresh Install a genuinely separate feature. It persists its own report/history,
# but it does not create a normal Clean/Rebuild project after installation.
base._fresh_worker = _safe_fresh_worker


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
