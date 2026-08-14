from __future__ import annotations

from pathlib import Path

from wpclean import rebuild_entry
from wpclean.theme_restore import ChildThemeScan


class DummyProfile:
    host = "example.com"
    remote_path = "/public_html"


def _write_flatsome_child_sql(backup_root: Path, child: str = "duyanhweb") -> None:
    sql = backup_root / "clean" / "database" / "clean.sql"
    sql.parent.mkdir(parents=True, exist_ok=True)
    sql.write_text(
        "INSERT INTO `wp_options` VALUES "
        "('1','template','flatsome','yes'),\n"
        f"('2','stylesheet','{child}','yes');\n",
        encoding="utf-8",
    )


def _write_clean_child(backup_root: Path, child: str = "duyanhweb") -> Path:
    root = backup_root / "themes" / child
    root.mkdir(parents=True, exist_ok=True)
    (root / "style.css").write_text(
        "/*\nTheme Name: Duy Anh Web\nTemplate: flatsome\n*/\n",
        encoding="utf-8",
    )
    (root / "functions.php").write_text(
        "<?php add_action('wp_enqueue_scripts', function () { wp_enqueue_style('child', get_stylesheet_uri()); });\n",
        encoding="utf-8",
    )
    return root


def test_declining_child_theme_skips_scan_and_upload(tmp_path: Path, monkeypatch):
    backup_root = tmp_path / "backup"
    _write_flatsome_child_sql(backup_root)
    _write_clean_child(backup_root)

    answers = iter([True, False])
    monkeypatch.setattr(rebuild_entry.typer, "confirm", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(rebuild_entry, "install_flatsome", lambda *args, **kwargs: (10, "a" * 64))

    def fail_scan(*args, **kwargs):
        raise AssertionError("child scanner must not run when user chooses N")

    def fail_upload(*args, **kwargs):
        raise AssertionError("child upload must not run when user chooses N")

    monkeypatch.setattr(rebuild_entry, "scan_child_theme", fail_scan)
    monkeypatch.setattr(rebuild_entry, "install_child_theme", fail_upload)

    result = rebuild_entry._run_theme_stage(
        profile=DummyProfile(),
        transport=object(),
        backup_root=backup_root,
        report_path=tmp_path / "report.json",
    )

    assert result.child_prompted is True
    assert result.child_scan is None
    assert result.child_installed is False


def test_accepting_clean_child_scans_then_uploads_without_second_prompt(tmp_path: Path, monkeypatch):
    backup_root = tmp_path / "backup"
    _write_flatsome_child_sql(backup_root)
    child_root = _write_clean_child(backup_root)

    answers = iter([True, True])
    monkeypatch.setattr(rebuild_entry.typer, "confirm", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(rebuild_entry, "install_flatsome", lambda *args, **kwargs: (10, "b" * 64))

    events: list[str] = []

    def fake_scan(root, *, slug=None, backup_root=None):
        events.append("scan")
        assert root == child_root
        assert backup_root == tmp_path / "backup"
        return ChildThemeScan(slug=slug or root.name, path=str(root), files_scanned=2)

    def fake_upload(profile, transport, root, slug, *, progress=None):
        events.append("upload")
        assert root == child_root
        assert slug == "duyanhweb"
        return 2

    monkeypatch.setattr(rebuild_entry, "scan_child_theme", fake_scan)
    monkeypatch.setattr(rebuild_entry, "install_child_theme", fake_upload)

    result = rebuild_entry._run_theme_stage(
        profile=DummyProfile(),
        transport=object(),
        backup_root=backup_root,
        report_path=tmp_path / "report.json",
    )

    assert events == ["scan", "upload"]
    assert result.child_prompted is True
    assert result.child_installed is True
    assert result.child_files_uploaded == 2


def test_accepting_suspicious_child_blocks_upload_and_creates_repair_workspace(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    backup_root = tmp_path / "backup"
    _write_flatsome_child_sql(backup_root)
    child_root = backup_root / "themes" / "duyanhweb"
    child_root.mkdir(parents=True, exist_ok=True)
    (child_root / "style.css").write_text(
        "/*\nTheme Name: Duy Anh Web\nTemplate: flatsome\n*/\n",
        encoding="utf-8",
    )
    (child_root / "functions.php").write_text(
        "<?php eval(gzinflate(base64_decode($_POST['payload'])));",
        encoding="utf-8",
    )

    answers = iter([True, True])
    monkeypatch.setattr(rebuild_entry.typer, "confirm", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(rebuild_entry, "install_flatsome", lambda *args, **kwargs: (10, "c" * 64))

    def fail_upload(*args, **kwargs):
        raise AssertionError("blocked child theme must never be uploaded")

    monkeypatch.setattr(rebuild_entry, "install_child_theme", fail_upload)

    result = rebuild_entry._run_theme_stage(
        profile=DummyProfile(),
        transport=object(),
        backup_root=backup_root,
        report_path=tmp_path / "report.json",
    )

    output = capsys.readouterr().out
    assert result.child_installed is False
    assert result.child_scan is not None
    assert result.child_scan["blocked"] is True
    assert result.child_repair_workspace == "repairs/backup/themes/duyanhweb/working-copy"
    assert Path(result.child_repair_workspace).is_dir()
    assert "Theme cần sửa:" in output
    assert "Danh sách file nghi vấn:" in output
    assert "Backup gốc giữ nguyên:" in output
