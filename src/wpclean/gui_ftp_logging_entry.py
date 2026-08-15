from __future__ import annotations

from datetime import datetime

from . import gui_journal_entry  # noqa: F401 - activate normal GUI + persistent project journal
from . import gui_server as server
from . import gui_ui


_BASE_RENDER = gui_ui.render_app
_BASE_TEST_CONNECTION = server.test_connection


_FTP_TEST_JS = r'''
function ftpTestClientLine(text){
  const out=qs('#terminalOutput');
  if(!out)return;
  if(out.classList.contains('terminal-empty')){
    out.textContent='';
    out.classList.remove('terminal-empty');
  }
  const current=out.textContent||'';
  out.textContent=current+(current?'\n':'')+text;
  out.scrollTop=out.scrollHeight;
}
async function ftpRefreshProject(name){
  try{
    const p=await api('/api/projects/'+encodeURIComponent(name));
    state.currentProject=p;
    if(state.selected===name)renderDrawer(p);
    return p;
  }catch(_e){return null}
}
testFtp=async function(name){
  const p=(state.currentProject&&state.currentProject.name===name)?state.currentProject:null;
  const c=(p&&p.connection)||{};
  const host=c.host||(p&&p.host)||'FTP';
  const port=c.port||'';
  const now=new Date().toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  ftpTestClientLine(`[${now}] FTP TEST · đang kết nối ${host}${port?':'+port:''} ...`);
  toast('Đang kiểm tra FTP...');
  try{
    const d=await api('/api/projects/'+encodeURIComponent(name)+'/test',{method:'POST',body:'{}'});
    await ftpRefreshProject(name);
    toast('Kết nối FTP thành công · '+d.remotePath);
  }catch(e){
    await ftpRefreshProject(name);
    toast(e.message,true);
    if(authError(e.message))openEditFtp(name,true);
  }
}
'''


def _render_with_ftp_test_log(token: str) -> str:
    html = _BASE_RENDER(token)
    return html.replace("</script>\n</body>", _FTP_TEST_JS + "\n</script>\n</body>", 1)


def _ftp_error_message(exc: Exception, *, host: str, port: int) -> str:
    raw = f"{type(exc).__name__}: {exc}"
    text = raw.lower()
    endpoint = f"{host}:{port}"

    if "10060" in text or "timed out" in text or "timeout" in text:
        return (
            f"FTP TEST · TIMEOUT · không nhận phản hồi từ {endpoint}. "
            "Kiểm tra FTP host, port, firewall hoặc IP đang bị hosting chặn."
        )
    if "530" in text or "login authentication failed" in text or "authentication failed" in text or "not logged in" in text:
        return (
            f"FTP TEST · LOGIN FAILED · {endpoint} đã phản hồi nhưng từ chối tài khoản/mật khẩu FTP."
        )
    if "10061" in text or "connection refused" in text or "actively refused" in text:
        return (
            f"FTP TEST · CONNECTION REFUSED · {endpoint} từ chối kết nối. "
            "Kiểm tra port hoặc dịch vụ FTP trên hosting."
        )
    if "getaddrinfo" in text or "name or service not known" in text or "nodename nor servname" in text:
        return f"FTP TEST · DNS ERROR · không phân giải được FTP host {host}."
    if "ssl" in text or "tls" in text or "certificate" in text:
        return f"FTP TEST · TLS ERROR · không thiết lập được kết nối FTPS tới {endpoint}: {exc}"
    return f"FTP TEST · FAIL · {raw}"


def _diagnostic_job(name: str, profile):
    report_dir = server.REPORTS_DIR / profile.host
    with server.JOBS_LOCK:
        job = server.JOBS.get(name)
        if job is None:
            job = server.GuiJob(project=name, status="idle", stage="ftp-test", title="Kiểm tra FTP", message="Sẵn sàng")
            server.JOBS[name] = job
    job._journal_dir = report_dir
    job._journal_secrets = tuple(value for value in (profile.password or "",) if value)
    job._journal_session = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-ftp")
    return job


def _test_connection_with_log(name: str):
    _profile_path, profile, _paths = server._profile_and_paths(name)
    job = _diagnostic_job(name, profile)

    if job.status == "running" and server.ACTIVE_PROJECT == name:
        raise RuntimeError("Dự án đang chạy workflow. Hãy chờ bước hiện tại dừng rồi kiểm tra FTP riêng.")

    previous_stage = job.stage
    try:
        job.stage = "ftp-test"
        job.log(f"FTP TEST · bắt đầu kết nối {profile.host}:{profile.port} · {profile.protocol.upper()}")
        result = _BASE_TEST_CONNECTION(name)
        job.log(f"FTP TEST · PASS · remote {profile.remote_path} · cwd {result.get('cwd') or '/'}")
        return result
    except Exception as exc:
        message = _ftp_error_message(exc, host=profile.host, port=profile.port)
        job.log("ERROR | " + message)
        raise RuntimeError(message) from exc
    finally:
        job.stage = previous_stage


gui_ui.render_app = _render_with_ftp_test_log
server.test_connection = _test_connection_with_log


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
