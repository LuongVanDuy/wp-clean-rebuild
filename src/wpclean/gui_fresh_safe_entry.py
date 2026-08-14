from __future__ import annotations

from http.client import HTTPResponse
from pathlib import PurePosixPath
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import secrets
import traceback

from . import fresh_install as fresh_module
from . import gui_fresh_entry as base
from . import gui_server as server
from . import gui_ui
from .fresh_install import FreshInstallRequest, _database_create_bridge, _encoded_config
from .rebuild_execute import _delete_remote_file, _ensure_remote_dir, _php_single_quote, _upload_text


_ORIGINAL_RUN_FRESH_INSTALL = base.run_fresh_install
_ORIGINAL_START_FRESH = base.start_fresh_install
_ORIGINAL_START_CLEAN = server.start_job
_BASE_SAFE_RENDER = gui_ui.render_app


_FRESH_SAFE_UX_CSS = r'''<style id="wpclean-fresh-safe-ux-style">
.fresh-modal .modalbox{position:relative;height:min(920px,calc(100vh - 24px));max-height:calc(100vh - 24px);display:flex;flex-direction:column;overflow:hidden}
.fresh-modal .fresh-head{flex:0 0 auto}
.fresh-modal .fresh-body{flex:1 1 auto;min-height:0;max-height:none!important;overflow-y:auto!important;padding-bottom:92px!important}
.fresh-modal .fresh-actions{position:absolute!important;left:0;right:0;bottom:0!important;margin:0!important;padding:14px 24px!important;background:#fff;border-top:1px solid var(--line);box-shadow:0 -8px 22px rgba(30,42,60,.08);z-index:30;min-height:66px;align-items:center}
.fresh-auto-note{grid-column:1/-1;border:1px solid #dbe4f1;background:#f7faff;border-radius:8px;padding:11px 12px;color:#526174;font-size:14px;line-height:1.5}
.fresh-auto-note b{font-weight:600;color:#26364c}.fresh-auto-note code{font-size:13px}
.fresh-retry-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
@media(max-width:760px){.fresh-modal .modalbox{height:calc(100vh - 12px);max-height:calc(100vh - 12px)}.fresh-modal .fresh-body{padding-bottom:82px!important}.fresh-modal .fresh-actions{padding:12px 14px!important;min-height:60px}}
</style>'''


_FRESH_SAFE_UX_JS = r'''
function freshEditAfterError(){
  if(freshPoll){clearInterval(freshPoll);freshPoll=null}
  qs('#freshFormFields').style.display='block';
  qs('#freshProgress').classList.remove('show');
  qs('#fi_submit').style.display='inline-flex';
  qs('#fi_submit').disabled=false;
  const body=qs('#freshModal .fresh-body');
  if(body)body.scrollTop=0;
}
const _renderFreshJobSafeUx=renderFreshJob;
renderFreshJob=function(j){
  _renderFreshJobSafeUx(j);
  if(j && j.status==='error'){
    const result=qs('#fi_result');
    if(result){
      result.insertAdjacentHTML('beforeend','<div class="fresh-retry-actions"><button class="btn btn-primary" type="button" onclick="freshEditAfterError()">Sửa thông tin & thử lại</button><button class="btn btn-light" type="button" onclick="closeFreshInstall()">Đóng</button></div>');
    }
  }
}
'''


def _render_with_safe_fresh_ux(token: str) -> str:
    html = _BASE_SAFE_RENDER(token)
    html = html.replace(
        "FTP không tự tạo MySQL database. Chọn dùng DB có sẵn hoặc cấp MySQL account có quyền CREATE DATABASE/CREATE USER.",
        "Tool sẽ thử tạo database qua PHP/MySQL. Nếu hosting không cấp quyền CREATE DATABASE, hãy tạo DB trong hosting panel rồi chuyển sang chế độ dùng database có sẵn.",
        1,
    )
    html = html.replace("Tạo database + user mới", "Tạo database tự động qua PHP", 1)
    html = html.replace("<label>MySQL admin user</label>", "<label>MySQL admin user (tùy chọn)</label>", 1)
    html = html.replace("<label>MySQL admin password</label>", "<label>MySQL admin password (tùy chọn)</label>", 1)
    html = html.replace(
        '<div id="fi_dbadmin" class="fresh-grid fresh-db-admin" style="margin-top:12px">',
        '<div id="fi_dbadmin" class="fresh-grid fresh-db-admin" style="margin-top:12px"><div class="fresh-auto-note"><b>Có thể để trống MySQL admin.</b> Khi để trống, tool sẽ tự thử dùng <code>Database user + Database password</code> phía trên để tạo database. Nếu MySQL từ chối quyền, hãy tạo database/user trong cPanel, DirectAdmin hoặc aaPanel rồi chọn “Dùng database có sẵn”.</div>',
        1,
    )
    html = html.replace("</head>", _FRESH_SAFE_UX_CSS + "\n</head>", 1)
    return html.replace("</script>\n</body>", _FRESH_SAFE_UX_JS + "\n</script>\n</body>", 1)


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
$tmp='wpclean_priv_'.bin2hex(random_bytes(4));
if(!$db->query("CREATE TABLE `{$tmp}` (`id` INT NOT NULL PRIMARY KEY) ENGINE=InnoDB")){out(false,'Database user cannot create WordPress tables: '.$db->error,array(),500);}
if(!$db->query("INSERT INTO `{$tmp}` (`id`) VALUES (1)")){@$db->query("DROP TABLE `{$tmp}`");out(false,'Database user cannot write data: '.$db->error,array(),500);}
if(!$db->query("DROP TABLE `{$tmp}`")){out(false,'Database privilege test cleanup failed: '.$db->error,array(),500);}
out(true,'database connection ready, empty and writable',array('database'=>$cfg['name'],'tables'=>0));
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


def _decode_bridge_response(body: bytes, status: int) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        snippet = body.decode("utf-8", errors="replace").strip()[:260]
        detail = f" · {snippet}" if snippet else ""
        raise RuntimeError(f"PHP installer trả HTTP {status} nhưng không có JSON hợp lệ{detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"PHP installer trả dữ liệu không hợp lệ · HTTP {status}")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("message") or f"PHP installer failed · HTTP {status}"))
    if status >= 400:
        raise RuntimeError(str(payload.get("message") or f"HTTP {status}"))
    return payload


def _call_bridge_detailed(site_url: str, filename: str, token: str) -> dict:
    url = site_url.rstrip("/") + "/" + filename
    request = Request(
        url,
        data=b"{}",
        headers={
            "X-WPClean-Token": token,
            "Content-Type": "application/json",
            "User-Agent": "WP-Clean-Rebuild/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            return _decode_bridge_response(response.read(1024 * 1024), int(response.status))
    except HTTPError as exc:
        body = exc.read(1024 * 1024)
        return _decode_bridge_response(body, int(exc.code))


def _upload_and_call(transport, req: FreshInstallRequest, php_factory) -> None:
    token = secrets.token_urlsafe(32)
    filename = f"wpclean-preflight-{secrets.token_hex(8)}.php"
    remote = str(PurePosixPath(req.remote_path) / filename)
    _upload_text(transport, remote, php_factory(token))
    try:
        _call_bridge_detailed(req.site_url, filename, token)
    finally:
        _delete_remote_file(transport, remote)


def _preflight_remote(req: FreshInstallRequest, transport, job) -> None:
    _ensure_root(transport, req.remote_path)
    entries = fresh_module._remote_entries(transport, req.remote_path)
    if entries and not req.wipe_existing:
        raise RuntimeError(
            "Thư mục đích không rỗng. Fresh Install chưa tạo database và chưa xóa dữ liệu. "
            "Nếu đây đúng là hosting cần cài mới hoàn toàn, bật 'Xóa dữ liệu hiện có' và nhập lại domain."
        )
    if entries:
        job.log(f"Preflight remote: phát hiện {len(entries)} mục; đã có xác nhận xóa theo domain", req.secrets)
    else:
        job.log("Preflight remote PASS · thư mục đích đang rỗng", req.secrets)


def _using_database_user_as_creator(req: FreshInstallRequest) -> bool:
    return (
        req.db_mode == "create"
        and req.mysql_admin_user == req.db_user
        and req.mysql_admin_password == req.db_password
    )


def _preflight_database(req: FreshInstallRequest, transport, job) -> None:
    if req.db_mode == "create":
        if _using_database_user_as_creator(req):
            job.log("Preflight: không có MySQL admin · đang thử dùng chính Database user để tạo database", req.secrets)
        else:
            job.log("Preflight: dùng MySQL admin để tạo database/user trước destructive boundary", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_create_bridge(req, token))
        job.log("Preflight: xác minh database user đăng nhập được, database rỗng và có quyền ghi", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_test_bridge(req, token))
    else:
        job.log("Preflight: kiểm tra database có sẵn trước destructive boundary", req.secrets)
        _upload_and_call(transport, req, lambda token: _database_test_bridge(req, token))
    job.log("Preflight database PASS · kết nối được, database rỗng và có quyền cài WordPress", req.secrets)


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
        return _call_bridge_detailed(site_url, _visible_bridge_name(filename), token)

    fresh_module._upload_text = upload_visible
    fresh_module._delete_remote_file = delete_visible
    fresh_module._call_bridge = call_visible
    try:
        return _ORIGINAL_RUN_FRESH_INSTALL(*args, **kwargs)
    finally:
        fresh_module._upload_text = original_upload
        fresh_module._delete_remote_file = original_delete
        fresh_module._call_bridge = original_call


def _database_creation_hint(req: FreshInstallRequest, exc: Exception) -> str | None:
    if req.db_mode != "create":
        return None
    text = str(exc).lower()
    mysql_markers = (
        "mysql admin login failed",
        "create database failed",
        "create user failed",
        "grant failed",
        "access denied",
        "database login failed",
    )
    if not any(marker in text for marker in mysql_markers):
        return None
    if _using_database_user_as_creator(req):
        return (
            "Hosting không cho Database user hiện tại tự tạo database/user qua MySQL. "
            "PHP bridge đã chạy nhưng MySQL từ chối quyền. Hãy tạo database + user trong cPanel/DirectAdmin/aaPanel, "
            "sau đó chọn 'Dùng database có sẵn'; hoặc nhập MySQL admin có quyền CREATE DATABASE/CREATE USER."
        )
    return (
        "Tài khoản MySQL admin không đăng nhập được hoặc không đủ quyền CREATE DATABASE/CREATE USER/GRANT. "
        "Kiểm tra lại MySQL admin, hoặc tạo database trong hosting panel rồi chọn 'Dùng database có sẵn'."
    )


def _safe_fresh_worker(job, req: FreshInstallRequest) -> None:
    report_dir = server.REPORTS_DIR / req.site_host
    try:
        transport = server.wizard._transport(req.profile, req.ftp_password)
        job.message = "Đang preflight FTP / remote path / database trước khi thay đổi hosting"
        job.percent = 2
        transport.test_connection()
        _preflight_remote(req, transport, job)
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
        friendly = _database_creation_hint(req, exc) if not destructive_started else None
        prefix = "Fresh Install" if destructive_started else "Preflight"
        job.error = friendly or f"{prefix} {type(exc).__name__}: {exc}"
        job.message = (
            "Cài đặt đã dừng sau khi bắt đầu thay đổi hosting; kiểm tra log trước khi chạy lại."
            if destructive_started
            else "Dừng trước destructive boundary; hosting chưa bị wipe bởi Fresh Install."
        )
        job.log(job.error, req.secrets)
        job.log(traceback.format_exc(limit=4), req.secrets)


def _normalize_fresh_request_data(data: dict) -> dict:
    normalized = dict(data)
    if str(normalized.get("dbMode") or "existing").strip().lower() != "create":
        return normalized

    admin_user = str(normalized.get("mysqlAdminUser") or "").strip()
    admin_password = str(normalized.get("mysqlAdminPassword") or "")
    if bool(admin_user) != bool(admin_password):
        raise ValueError("Nếu dùng MySQL admin, hãy nhập đủ cả username và password; hoặc để trống cả hai để tool tự thử Database user.")

    if not admin_user:
        normalized["mysqlAdminHost"] = str(normalized.get("mysqlAdminHost") or normalized.get("dbHost") or "localhost").strip()
        normalized["mysqlAdminUser"] = str(normalized.get("dbUser") or "").strip()
        normalized["mysqlAdminPassword"] = str(normalized.get("dbPassword") or "")
    return normalized


def _start_fresh_serialized(data):
    if server.ACTIVE_PROJECT:
        raise RuntimeError(
            f"Đang chạy Clean/Rebuild cho dự án {server.ACTIVE_PROJECT}. Hãy chờ hoàn tất trước khi Fresh Install."
        )
    return _ORIGINAL_START_FRESH(_normalize_fresh_request_data(data))


def _start_clean_serialized(name, options):
    if any(job.status == "running" for job in base.FRESH_JOBS.values()):
        raise RuntimeError("Đang chạy Fresh Install. Hãy chờ cài WordPress mới hoàn tất trước khi chạy Clean/Rebuild.")
    return _ORIGINAL_START_CLEAN(name, options)


# Keep Fresh Install a genuinely separate feature. It persists its own report/history,
# but it does not create a normal Clean/Rebuild project after installation.
base._fresh_worker = _safe_fresh_worker
base.start_fresh_install = _start_fresh_serialized
server.start_job = _start_clean_serialized
gui_ui.render_app = _render_with_safe_fresh_ux


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
