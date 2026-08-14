from __future__ import annotations

from pathlib import Path

from wpclean.operator_entry import _next_stage
from wpclean.operator_wizard import _infer_status, _slug


def _paths(tmp_path: Path) -> dict[str, Path]:
    report_root = tmp_path / "reports" / "example.com"
    return {
        "profile": tmp_path / "sites" / "example.json",
        "backup": tmp_path / "backups" / "example.com",
        "state": report_root / "operator-state.json",
        "preflight": report_root / "rebuild-preflight.json",
        "execute": report_root / "rebuild-execute.json",
        "scan": report_root / "operator-scan.json",
        "final": report_root / "final-verify.json",
    }


def test_slug_makes_safe_project_filename():
    assert _slug(" TinyTensor VN / Test ") == "tinytensor-vn-test"
    assert _slug("***") == "du-an"


def test_new_project_starts_with_filesystem_backup(tmp_path: Path):
    status = _infer_status(_paths(tmp_path))
    assert _next_stage(status) == "backup-files"


def test_resume_after_rebuild_goes_to_theme_not_backup(tmp_path: Path):
    paths = _paths(tmp_path)
    paths["execute"].parent.mkdir(parents=True, exist_ok=True)
    paths["execute"].write_text(
        """{
          "database_imported": true,
          "wp_config_uploaded": true,
          "htaccess_uploaded": true,
          "core_uploaded": 1200
        }""",
        encoding="utf-8",
    )

    status = _infer_status(paths)
    assert status["rebuild_ready"] is True
    assert _next_stage(status) == "theme"


def test_child_theme_repair_resumes_at_theme(tmp_path: Path):
    paths = _paths(tmp_path)
    paths["execute"].parent.mkdir(parents=True, exist_ok=True)
    paths["execute"].write_text(
        """{
          "database_imported": true,
          "wp_config_uploaded": true,
          "htaccess_uploaded": true,
          "core_uploaded": 1200,
          "theme_stage": {
            "mode": "flatsome-child",
            "flatsome_installed": true,
            "child_theme_detected": true,
            "child_installed": false,
            "child_repair_workspace": "repairs/example.com/themes/child/working-copy"
          }
        }""",
        encoding="utf-8",
    )

    status = _infer_status(paths)
    assert status["theme_repair"] is True
    assert _next_stage(status) == "theme"


def test_completed_theme_without_plugin_goes_to_plugin(tmp_path: Path):
    paths = _paths(tmp_path)
    paths["execute"].parent.mkdir(parents=True, exist_ok=True)
    paths["execute"].write_text(
        """{
          "database_imported": true,
          "wp_config_uploaded": true,
          "htaccess_uploaded": true,
          "core_uploaded": 1200,
          "theme_stage": {
            "mode": "flatsome",
            "flatsome_installed": true
          }
        }""",
        encoding="utf-8",
    )

    status = _infer_status(paths)
    assert status["theme_done"] is True
    assert _next_stage(status) == "plugin"


def test_final_pass_marks_project_done(tmp_path: Path):
    paths = _paths(tmp_path)
    paths["final"].parent.mkdir(parents=True, exist_ok=True)
    paths["final"].write_text('{"status":"PASS"}', encoding="utf-8")

    status = _infer_status(paths)
    assert _next_stage(status) == "done"
