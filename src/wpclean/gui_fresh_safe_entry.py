from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
import secrets

from . import gui_fresh_entry as base
from . import gui_server as server
from .fresh_install import FreshInstallRequest, _call_bridge, _database_create_bridge, _encoded_config
from .rebuild_execute import _delete_remote_file, _ensure_remote_dir, _php_single_quote, _upload_text


_BASE_FRESH_WORKER = base._fresh_worker


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
out(true,'database connection ready',array('database'=>$cfg['name']));
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


def _preflight_database(req: FreshInstallRequest, transport, job) -> None:
    _ensure_root(transport, req.remote_path)
    token = secrets.token_urlsafe(32)
    filename = f".wpclean-preflight-{secrets.token_hex(8)}.php"
    remote = str(PurePosixPath(req.remote_path) / filename)
    if req.db_mode == "create":
        job.log("Preflight: kiểm tra quyền MySQL và tạo database/user trước destructive boundary", req.secrets)
        php = _database_create_bridge(req, token)
    else:
        job.log("Preflight: kiểm tra database có sẵn trước destructive boundary", req.secrets)
        php = _database_test_bridge(req, token)
    _upload_text(transport, remote, php)
    try:
        _call_bridge(req.site_url, filename, token)
        job.log("Preflight database PASS", req.secrets)
    finally:
        _delete_remote_file(transport, remote)


def _safe_fresh_worker(job, req: FreshInstallRequest) -> None:
    try:
        transport = server.wizard._transport(req.profile, req.ftp_password)
        job.message = "Đang preflight FTP / database trước khi thay đổi hosting"
        job.percent = 2
        transport.test_connection()
        _preflight_database(req, transport, job)
    except Exception as exc:
        job.status = "error"
        job.error = f"Preflight {type(exc).__name__}: {exc}"
        job.message = "Dừng trước destructive boundary; hosting chưa bị wipe bởi Fresh Install."
        job.log(job.error, req.secrets)
        return
    _BASE_FRESH_WORKER(job, req)


base._fresh_worker = _safe_fresh_worker


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
