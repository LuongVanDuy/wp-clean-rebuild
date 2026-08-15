from __future__ import annotations

from datetime import datetime
import threading

from . import gui_journal_entry  # noqa: F401 - activate normal GUI + persistent project journal
from .gui_observability import OperationError, classify_exception
from . import gui_server as server
from . import gui_ui


_BASE_RENDER = gui_ui.render_app
_BASE_TEST_CONNECTION = server.test_connection
_FTP_TEST_LOCK = threading.Lock()
_FTP_TEST_ACTIVE: set[str] = set()


_FTP_TEST_JS = r'''
const ftpTestsInFlight=new Set();
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
  if(ftpTestsInFlight.has(name)){
    toast('FTP đang được kiểm tra. Vui lòng chờ kết quả hiện tại.');
    return;
  }
  ftpTestsInFlight.add(name);
  const done=beginBusy('Đang kiểm tra...');
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
    if(authError(e.message)||e.code==='FTP-AUTH-001')openEditFtp(name,true);
  }finally{
    ftpTestsInFlight.delete(name);
    done();
  }
}
'''


def _render_with_ftp_test_log(token: str) -> str:
    html = _BASE_RENDER(token)
    return html.replace("</script>\n</body>", _FTP_TEST_JS + "\n</script>\n</body>", 1)


def _concise_job_log(self, text: str) -> None:
    """Compatibility helper; GuiJob now owns concise, structured logging."""
    self.log(text)


def _ftp_error_message(exc: Exception, *, host: str, port: int) -> str:
    info = classify_exception(exc, stage="ftp-test")
    return f"{info.operator_line()} · Hướng xử lý: {info.recovery} · Đích: {host}:{port}"


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


def _claim_ftp_test(name: str) -> bool:
    with _FTP_TEST_LOCK:
        if name in _FTP_TEST_ACTIVE:
            return False
        _FTP_TEST_ACTIVE.add(name)
        return True


def _release_ftp_test(name: str) -> None:
    with _FTP_TEST_LOCK:
        _FTP_TEST_ACTIVE.discard(name)


def _test_connection_with_log(name: str):
    _profile_path, profile, _paths = server._profile_and_paths(name)
    job = _diagnostic_job(name, profile)

    if job.status == "running" and server.ACTIVE_PROJECT == name:
        raise RuntimeError("Dự án đang chạy workflow. Hãy chờ bước hiện tại dừng rồi kiểm tra FTP riêng.")
    if not _claim_ftp_test(name):
        raise RuntimeError("FTP TEST đang chạy cho dự án này. Vui lòng chờ kết quả hiện tại.")

    try:
        job.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        job.set(
            status="running",
            stage="ftp-test",
            title="Kiểm tra kết nối FTP",
            message=f"Đang kết nối {profile.host}:{profile.port}",
            percent=10,
            current=profile.remote_path,
            error="",
            error_code="",
            error_title="",
            recovery="",
            technical_error="",
        )
        job.log(f"FTP TEST · bắt đầu kết nối {profile.host}:{profile.port} · {profile.protocol.upper()}")
        result = _BASE_TEST_CONNECTION(name)
        job.log(f"FTP TEST · PASS · remote {profile.remote_path} · cwd {result.get('cwd') or '/'}")
        job.set(status="idle", title="Kiểm tra FTP thành công", message="Hosting và remote path đều truy cập được.", percent=100)
        return result
    except Exception as exc:
        info = classify_exception(exc, stage="ftp-test")
        job.fail(OperationError(info))
        raise OperationError(info) from exc
    finally:
        _release_ftp_test(name)


gui_ui.render_app = _render_with_ftp_test_log
server.test_connection = _test_connection_with_log


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
