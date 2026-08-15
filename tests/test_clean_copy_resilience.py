from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wpclean import clean_builder
from wpclean import gui_ftp_logging_entry as ftp_gui
from wpclean import gui_server


def test_clean_upload_copy_retries_transient_missing_path(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "uploads"
    destination = tmp_path / "clean" / "uploads"
    source.mkdir(parents=True)
    photo = source / "photo.jpg"
    photo.write_bytes(b"image-data")

    real_copy2 = shutil.copy2
    calls = {"count": 0}

    def flaky_copy(src, dst, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileNotFoundError(3, "The system cannot find the path specified")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(clean_builder, "scan_uploads", lambda _source: [])
    monkeypatch.setattr(clean_builder.shutil, "copy2", flaky_copy)

    copied, dropped = clean_builder._copy_clean_uploads(source, destination)

    assert copied == 1
    assert dropped == []
    assert calls["count"] == 2
    assert (destination / "photo.jpg").read_bytes() == b"image-data"


def test_clean_upload_copy_reports_when_backup_source_disappears(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "uploads"
    destination = tmp_path / "clean" / "uploads"
    source.mkdir(parents=True)
    photo = source / "photo.jpg"
    photo.write_bytes(b"image-data")

    def quarantine_copy(src, _dst, *args, **kwargs):
        Path(src).unlink(missing_ok=True)
        raise FileNotFoundError(3, "The system cannot find the path specified")

    monkeypatch.setattr(clean_builder, "scan_uploads", lambda _source: [])
    monkeypatch.setattr(clean_builder.shutil, "copy2", quarantine_copy)

    with pytest.raises(RuntimeError, match="File backup đã biến mất") as exc_info:
        clean_builder._copy_clean_uploads(source, destination)

    assert "antivirus" in str(exc_info.value).lower()
    assert "uploads/photo.jpg" in str(exc_info.value)


def test_production_gui_collapses_python_traceback_to_one_error_line() -> None:
    job = gui_server.GuiJob(project="demo")
    raw = """Traceback (most recent call last):
  File \"gui_server.py\", line 1, in demo
    copy()
FileNotFoundError: [WinError 3] The system cannot find the path specified
"""

    ftp_gui._concise_job_log(job, raw)

    assert len(job.logs) == 1
    assert "Traceback" not in job.logs[0]
    assert "FileNotFoundError" in job.logs[0]
    assert "WinError 3" in job.logs[0]
