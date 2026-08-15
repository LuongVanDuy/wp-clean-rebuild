from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest
import typer

from wpclean import gui_parallel_entry as parallel_gui
from wpclean import gui_server


def _sandbox(tmp_path: Path, monkeypatch) -> None:
    sites = tmp_path / "sites"
    backups = tmp_path / "backups"
    reports = tmp_path / "reports"
    repairs = tmp_path / "repairs"
    for path in (sites, backups, reports, repairs):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(gui_server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gui_server, "SITES_DIR", sites)
    monkeypatch.setattr(gui_server, "BACKUPS_DIR", backups)
    monkeypatch.setattr(gui_server, "REPORTS_DIR", reports)
    monkeypatch.setattr(gui_server, "REPAIRS_DIR", repairs)
    monkeypatch.setattr(gui_server.wizard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gui_server.wizard, "SITES_DIR", sites)
    monkeypatch.setattr(gui_server.wizard, "BACKUPS_DIR", backups)
    monkeypatch.setattr(gui_server.wizard, "REPORTS_DIR", reports)
    gui_server.JOBS.clear()
    with gui_server.JOBS_LOCK:
        parallel_gui._ACTIVE_TARGETS.clear()


def _create(name: str, host: str, *, remote: str | None = None) -> None:
    gui_server.create_project(
        {
            "name": name,
            "host": host,
            "username": "ftp-user",
            "password": "ftp-pass",
            "protocol": "ftp",
            "port": 21,
            "remotePath": remote or f"/domains/{host}/public_html",
            "siteUrl": f"https://{host}",
        }
    )


def test_different_projects_can_run_at_the_same_time(tmp_path: Path, monkeypatch) -> None:
    _sandbox(tmp_path, monkeypatch)
    _create("site-a", "a.example.test")
    _create("site-b", "b.example.test")

    started = {"site-a": threading.Event(), "site-b": threading.Event()}
    release = threading.Event()

    def fake_pipeline(name, _options, job):
        started[name].set()
        release.wait(timeout=3)
        job.set(status="success", percent=100, message="done")

    monkeypatch.setattr(gui_server, "_run_pipeline", fake_pipeline)

    a = gui_server.start_job("site-a", {})
    b = gui_server.start_job("site-b", {})
    assert a is not b
    assert started["site-a"].wait(timeout=1)
    assert started["site-b"].wait(timeout=1), "second project should start without waiting for the first"
    assert a.status == "running"
    assert b.status == "running"

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and (a.status == "running" or b.status == "running"):
        time.sleep(0.01)
    assert a.status == "success"
    assert b.status == "success"


def test_same_project_keeps_single_flight(tmp_path: Path, monkeypatch) -> None:
    _sandbox(tmp_path, monkeypatch)
    _create("site-a", "a.example.test")
    release = threading.Event()
    calls = []

    def fake_pipeline(name, _options, job):
        calls.append(name)
        release.wait(timeout=3)
        job.set(status="success")

    monkeypatch.setattr(gui_server, "_run_pipeline", fake_pipeline)
    first = gui_server.start_job("site-a", {})
    second = gui_server.start_job("site-a", {})
    assert first is second
    assert calls == ["site-a"]
    release.set()


def test_two_project_names_cannot_target_same_live_site_concurrently(tmp_path: Path, monkeypatch) -> None:
    _sandbox(tmp_path, monkeypatch)
    remote = "/domains/shared.example.test/public_html"
    _create("alias-a", "shared.example.test", remote=remote)
    _create("alias-b", "shared.example.test", remote=remote)
    release = threading.Event()

    def fake_pipeline(_name, _options, job):
        release.wait(timeout=3)
        job.set(status="success")

    monkeypatch.setattr(gui_server, "_run_pipeline", fake_pipeline)
    gui_server.start_job("alias-a", {})
    with pytest.raises(RuntimeError, match="cùng FTP host/remote path"):
        gui_server.start_job("alias-b", {})
    release.set()


def test_gui_confirm_answers_are_thread_local() -> None:
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}

    def worker(name: str, answer: bool) -> None:
        with parallel_gui._parallel_confirm_answers([answer]):
            barrier.wait(timeout=2)
            results[name] = typer.confirm("ignored in GUI", default=not answer)

    one = threading.Thread(target=worker, args=("yes", True))
    two = threading.Thread(target=worker, args=("no", False))
    one.start()
    two.start()
    one.join(timeout=2)
    two.join(timeout=2)

    assert results == {"yes": True, "no": False}


def test_thread_stream_router_does_not_cross_project_output() -> None:
    class Sink:
        encoding = "utf-8"

        def __init__(self):
            self.parts: list[str] = []

        def write(self, text):
            self.parts.append(str(text))
            return len(str(text))

        def flush(self):
            return None

        def isatty(self):
            return False

    fallback = Sink()
    router = parallel_gui._ThreadStreamRouter(fallback)
    a = Sink()
    b = Sink()
    barrier = threading.Barrier(2)

    def worker(sink: Sink, text: str) -> None:
        with router.route(sink):
            barrier.wait(timeout=2)
            router.write(text)

    ta = threading.Thread(target=worker, args=(a, "project-a"))
    tb = threading.Thread(target=worker, args=(b, "project-b"))
    ta.start()
    tb.start()
    ta.join(timeout=2)
    tb.join(timeout=2)

    assert "".join(a.parts) == "project-a"
    assert "".join(b.parts) == "project-b"
    assert fallback.parts == []


def test_db_only_resume_is_not_offered_after_wipe_or_partial_core(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    clean = backup / "clean"
    clean.mkdir(parents=True)
    (clean / "clean-report.json").write_text(json.dumps({"uploads_copied": 12}), encoding="utf-8")
    execute = tmp_path / "execute.json"
    paths = {"backup": backup, "execute": execute}

    execute.write_text(json.dumps({"wiped_files": 900, "core_uploaded": 0}), encoding="utf-8")
    assert parallel_gui._safe_rebuild_partial(paths) is False

    execute.write_text(
        json.dumps(
            {
                "wiped_files": 900,
                "core_uploaded": 1500,
                "wp_config_uploaded": True,
                "htaccess_uploaded": True,
                "uploads_uploaded": 4,
            }
        ),
        encoding="utf-8",
    )
    assert parallel_gui._safe_rebuild_partial(paths) is False


def test_db_only_resume_is_allowed_after_all_pre_db_uploads(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    clean = backup / "clean"
    clean.mkdir(parents=True)
    (clean / "clean-report.json").write_text(json.dumps({"uploads_copied": 12}), encoding="utf-8")
    execute = tmp_path / "execute.json"
    execute.write_text(
        json.dumps(
            {
                "core_uploaded": 1500,
                "wp_config_uploaded": True,
                "htaccess_uploaded": True,
                "uploads_uploaded": 12,
                "database_imported": False,
            }
        ),
        encoding="utf-8",
    )
    assert parallel_gui._safe_rebuild_partial({"backup": backup, "execute": execute}) is True


def test_parallel_gui_copy_mentions_multi_project_support() -> None:
    html = parallel_gui._render_parallel("test-token")
    assert "Có thể chạy nhiều website khác nhau cùng lúc." in html
