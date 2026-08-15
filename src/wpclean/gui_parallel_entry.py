from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any
import sys
import threading

import typer

from . import gui_ftp_logging_entry as ftp_gui  # noqa: F401 - activate production GUI stack
from . import gui_entry
from . import gui_journal_entry
from . import gui_server as server
from . import gui_ui


_ORIGINAL_TYPER_CONFIRM = typer.confirm
_CONFIRM_LOCAL = threading.local()
_ROUTER_LOCK = threading.Lock()
_STDOUT_ROUTER = None
_STDERR_ROUTER = None
_ACTIVE_TARGETS: dict[str, str] = {}
_BASE_TEST_CONNECTION = server.test_connection
_BASE_RENDER = gui_ui.render_app


class _ThreadStreamRouter:
    """Route stdout/stderr to the current GUI job without cross-project leakage.

    ``contextlib.redirect_stdout`` mutates ``sys.stdout`` for the whole process,
    so it is unsafe once multiple project threads run concurrently. This router
    is installed once and selects a destination from thread-local state.
    """

    def __init__(self, fallback) -> None:
        self.fallback = fallback
        self.local = threading.local()

    @contextmanager
    def route(self, target):
        previous = getattr(self.local, "target", None)
        self.local.target = target
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self.local.target
                except AttributeError:
                    pass
            else:
                self.local.target = previous

    def _target(self):
        return getattr(self.local, "target", None) or self.fallback

    def write(self, text):
        return self._target().write(text)

    def flush(self):
        return self._target().flush()

    def isatty(self) -> bool:
        try:
            return bool(self._target().isatty())
        except Exception:
            return False

    def fileno(self):
        return self._target().fileno()

    @property
    def encoding(self):
        return getattr(self._target(), "encoding", "utf-8") or "utf-8"

    def __getattr__(self, name: str):
        return getattr(self._target(), name)


def _ensure_stream_routers() -> tuple[_ThreadStreamRouter, _ThreadStreamRouter]:
    global _STDOUT_ROUTER, _STDERR_ROUTER
    with _ROUTER_LOCK:
        if not isinstance(sys.stdout, _ThreadStreamRouter):
            _STDOUT_ROUTER = _ThreadStreamRouter(sys.stdout)
            sys.stdout = _STDOUT_ROUTER
        else:
            _STDOUT_ROUTER = sys.stdout
        if not isinstance(sys.stderr, _ThreadStreamRouter):
            _STDERR_ROUTER = _ThreadStreamRouter(sys.stderr)
            sys.stderr = _STDERR_ROUTER
        else:
            _STDERR_ROUTER = sys.stderr
    return _STDOUT_ROUTER, _STDERR_ROUTER


def _thread_confirm(*args, default: bool = False, **kwargs) -> bool:
    stack = getattr(_CONFIRM_LOCAL, "stack", None)
    if stack:
        queue = stack[-1]
        if queue:
            return bool(queue.pop(0))
        return bool(default)
    return bool(_ORIGINAL_TYPER_CONFIRM(*args, default=default, **kwargs))


@contextmanager
def _parallel_confirm_answers(answers: list[bool]):
    stack = getattr(_CONFIRM_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _CONFIRM_LOCAL.stack = stack
    stack.append(list(answers))
    try:
        yield
    finally:
        stack.pop()
        if not stack:
            try:
                del _CONFIRM_LOCAL.stack
            except AttributeError:
                pass


def _target_key(profile) -> str:
    return f"{profile.protocol.lower()}://{profile.host.lower()}:{profile.port}{profile.remote_path.rstrip('/')}"


def _parallel_pipeline(name: str, options: dict[str, Any], job) -> None:
    """Run one project with project-local journal, stdout/stderr and confirmations."""
    report_dir, profile = gui_journal_entry._journal_dir(name)
    job._journal_dir = report_dir
    job._journal_secrets = tuple(value for value in (profile.password or "",) if value)
    job._journal_session = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")

    stdout_router, stderr_router = _ensure_stream_routers()
    stdout_stream = gui_entry._GuiTerminalStream(job, sys.__stdout__ or stdout_router.fallback)
    stderr_stream = gui_entry._GuiTerminalStream(job, sys.__stderr__ or stderr_router.fallback, prefix="ERROR | ")

    # One start line is enough. The older stack emitted both "phiên xử lý" and
    # "phiên xử lý GUI", which made operator logs look duplicated.
    job.log("Bắt đầu phiên xử lý")
    try:
        with stdout_router.route(stdout_stream), stderr_router.route(stderr_stream):
            gui_entry._ORIGINAL_RUN_PIPELINE(name, options, job)
    finally:
        stdout_stream.flush()
        stderr_stream.flush()
        job.log(f"Kết thúc phiên · trạng thái {job.status}")


def _release_target(name: str, target_key: str) -> None:
    with server.JOBS_LOCK:
        if _ACTIVE_TARGETS.get(target_key) == name:
            _ACTIVE_TARGETS.pop(target_key, None)


def _thread_main(name: str, options: dict[str, Any], job, target_key: str) -> None:
    try:
        server._run_pipeline(name, options, job)
    finally:
        _release_target(name, target_key)


def _parallel_start_job(name: str, options: dict[str, Any]):
    server._safe_project_path(name)
    _profile_path, profile, _paths = server._profile_and_paths(name)
    target_key = _target_key(profile)

    with server.JOBS_LOCK:
        existing = server.JOBS.get(name)
        if existing and existing.status == "running":
            return existing

        owner = _ACTIVE_TARGETS.get(target_key)
        if owner and owner != name:
            owner_job = server.JOBS.get(owner)
            if owner_job and owner_job.status == "running":
                raise RuntimeError(
                    f"Website này đang được xử lý trong dự án {owner}. "
                    "Không thể chạy hai workflow trên cùng FTP host/remote path cùng lúc."
                )
            _ACTIVE_TARGETS.pop(target_key, None)

        job = server.GuiJob(
            project=name,
            status="running",
            title="Đang bắt đầu",
            message="Chuẩn bị workflow",
        )
        job.started_at = datetime.now().isoformat(timespec="seconds")
        job.touch()
        server.JOBS[name] = job
        _ACTIVE_TARGETS[target_key] = name

    thread = threading.Thread(
        target=_thread_main,
        args=(name, options, job, target_key),
        daemon=True,
        name=f"wpclean-gui-{name}",
    )
    thread.start()
    return job


def _parallel_test_connection(name: str):
    job = server.JOBS.get(name)
    if job and job.status == "running":
        raise RuntimeError("Dự án đang chạy workflow. Hãy chờ bước hiện tại dừng rồi kiểm tra FTP riêng.")
    return _BASE_TEST_CONNECTION(name)


def _render_parallel(token: str) -> str:
    html = _BASE_RENDER(token)
    html = html.replace(
        "Chọn website và tiếp tục đúng bước đang dở.",
        "Chọn website và tiếp tục đúng bước đang dở. Có thể chạy nhiều website khác nhau cùng lúc.",
        1,
    )
    return html


# Install process-wide dispatchers once. The actual answer/output destinations
# are thread-local, so concurrent projects cannot consume each other's choices
# or terminal output.
typer.confirm = _thread_confirm
server._confirm_answers = _parallel_confirm_answers
server._run_pipeline = _parallel_pipeline
server.start_job = _parallel_start_job
server.test_connection = _parallel_test_connection
gui_ui.render_app = _render_parallel


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
