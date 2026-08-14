from __future__ import annotations

from pathlib import Path

from wpclean.theme_restore import (
    existing_child_theme_repair,
    prepare_child_theme_repair,
    scan_child_theme,
)


def _make_infected_child(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "style.css").write_text(
        "/*\nTheme Name: Duy Anh Web\nTemplate: flatsome\n*/\n",
        encoding="utf-8",
    )
    (root / "functions.php").write_text(
        "<?php eval(gzinflate(base64_decode($_POST['payload'])));",
        encoding="utf-8",
    )


def test_blocked_child_theme_creates_editable_copy_outside_backup(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backup_root = Path("backups") / "example.com"
    original = backup_root / "themes" / "duyanhweb"
    _make_infected_child(original)

    scan = scan_child_theme(original, slug="duyanhweb")
    assert scan.blocked is True

    working_copy, created = prepare_child_theme_repair(
        backup_root,
        original,
        scan,
        scan_root=original,
    )

    assert created is True
    assert working_copy == Path("repairs/example.com/themes/duyanhweb/working-copy")
    assert working_copy.is_dir()
    assert (working_copy / "functions.php").read_text(encoding="utf-8") == (
        original / "functions.php"
    ).read_text(encoding="utf-8")
    assert existing_child_theme_repair(backup_root, "duyanhweb") == working_copy
    assert (working_copy.parent / "SUSPECT_FILES.txt").is_file()
    suspect = (working_copy.parent / "SUSPECT_FILES.txt").read_text(encoding="utf-8")
    assert "functions.php" in suspect
    assert "KHONG SUA" in suspect
    assert (working_copy.parent / "scan-report.json").is_file()


def test_existing_repair_copy_is_never_overwritten(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backup_root = Path("backups") / "example.com"
    original = backup_root / "themes" / "duyanhweb"
    _make_infected_child(original)

    first_scan = scan_child_theme(original, slug="duyanhweb")
    working_copy, created = prepare_child_theme_repair(
        backup_root,
        original,
        first_scan,
        scan_root=original,
    )
    assert created is True

    repaired = "<?php\n// technician-cleaned child theme\n"
    (working_copy / "functions.php").write_text(repaired, encoding="utf-8")

    second_scan = scan_child_theme(working_copy, slug="duyanhweb")
    assert second_scan.blocked is False

    same_copy, created_again = prepare_child_theme_repair(
        backup_root,
        original,
        second_scan,
        scan_root=working_copy,
    )

    assert same_copy == working_copy
    assert created_again is False
    assert (working_copy / "functions.php").read_text(encoding="utf-8") == repaired
    assert "eval(gzinflate" in (original / "functions.php").read_text(encoding="utf-8")
