from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import wpclean.operator_entry as operator


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {"final": tmp_path / "reports" / "example.com" / "final-verify.json"}


def _profile():
    return SimpleNamespace(web_base_url="https://example.com", host="example.com")


def _ok_probe(url: str) -> dict:
    final = "https://example.com/wp-login.php" if url.endswith("/wp-admin/") else url
    return {"ok": True, "status": 200, "final_url": final, "error": ""}


def test_fast_final_marks_manual_completion_without_deep_scan(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(operator, "_http_probe", _ok_probe)
    monkeypatch.setattr(operator.Prompt, "ask", lambda *args, **kwargs: "1")

    status = operator._stage_final_fast(_profile(), object(), paths)

    assert status == "PASS WITH WARNINGS"
    payload = json.loads(paths["final"].read_text(encoding="utf-8"))
    assert payload["status"] == "PASS WITH WARNINGS"
    assert payload["mode"] == "operator-quick-check"
    assert payload["deep_scan_run"] is False
    assert payload["operator_confirmed"] is True
    assert payload["home"]["ok"] is True
    assert payload["admin"]["ok"] is True


def test_fast_final_can_pause_for_technical_fix(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(operator, "_http_probe", _ok_probe)
    monkeypatch.setattr(operator.Prompt, "ask", lambda *args, **kwargs: "2")

    with pytest.raises(operator.wizard.TamDungQuyTrinh):
        operator._stage_final_fast(_profile(), object(), paths)

    payload = json.loads(paths["final"].read_text(encoding="utf-8"))
    assert payload["status"] == "MANUAL REVIEW REQUIRED"
    assert payload["deep_scan_run"] is False
    assert payload["operator_confirmed"] is False


def test_fast_final_allows_explicit_deep_scan(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(operator, "_http_probe", _ok_probe)
    monkeypatch.setattr(operator.Prompt, "ask", lambda *args, **kwargs: "3")
    called = []

    def fake_deep(profile, transport, received_paths):
        called.append((profile, transport, received_paths))
        return "PASS"

    monkeypatch.setattr(operator, "_original_stage_final", fake_deep)

    status = operator._stage_final_fast(_profile(), object(), paths)

    assert status == "PASS"
    assert len(called) == 1
