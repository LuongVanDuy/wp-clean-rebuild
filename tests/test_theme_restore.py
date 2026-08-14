from __future__ import annotations

from pathlib import Path
import zipfile

from wpclean.theme_restore import (
    _safe_extract_flatsome,
    detect_active_theme,
    scan_child_theme,
)


def _write_clean_sql(path: Path, template: str, stylesheet: str) -> None:
    # db_bridge exports every non-NULL MySQL value quoted, including numeric option_id.
    path.write_text(
        "INSERT INTO `wp_options` VALUES "
        f"('1','template','{template}','yes'),\n"
        f"('2','stylesheet','{stylesheet}','yes');\n",
        encoding="utf-8",
    )


def test_detect_active_flatsome_without_child(tmp_path: Path):
    sql = tmp_path / "clean.sql"
    _write_clean_sql(sql, "flatsome", "flatsome")

    active = detect_active_theme(sql)

    assert active is not None
    assert active.is_flatsome is True
    assert active.has_child is False
    assert active.template == "flatsome"
    assert active.stylesheet == "flatsome"


def test_detect_active_flatsome_child(tmp_path: Path):
    sql = tmp_path / "clean.sql"
    _write_clean_sql(sql, "flatsome", "flatsome-child")

    active = detect_active_theme(sql)

    assert active is not None
    assert active.is_flatsome is True
    assert active.has_child is True
    assert active.stylesheet == "flatsome-child"


def test_detect_active_theme_still_accepts_unquoted_numeric_id(tmp_path: Path):
    sql = tmp_path / "clean.sql"
    sql.write_text(
        "INSERT INTO `wp_options` VALUES "
        "(1,'template','flatsome','yes'),"
        "(2,'stylesheet','flatsome','yes');\n",
        encoding="utf-8",
    )

    active = detect_active_theme(sql)

    assert active is not None
    assert active.template == "flatsome"
    assert active.stylesheet == "flatsome"


def test_clean_flatsome_child_passes_static_gate(tmp_path: Path):
    child = tmp_path / "flatsome-child"
    child.mkdir()
    (child / "style.css").write_text(
        "/*\nTheme Name: Client Child\nTemplate: flatsome\nVersion: 1.0\n*/\n",
        encoding="utf-8",
    )
    (child / "functions.php").write_text(
        "<?php\nadd_action('wp_enqueue_scripts', function () { wp_enqueue_style('child', get_stylesheet_uri()); });\n",
        encoding="utf-8",
    )

    report = scan_child_theme(child)

    assert report.files_scanned == 2
    assert report.blocked is False
    assert not [finding for finding in report.findings if finding.score >= 60]


def test_obfuscated_child_theme_is_blocked(tmp_path: Path, synthetic_code_samples):
    child = tmp_path / "flatsome-child"
    child.mkdir()
    (child / "style.css").write_text(
        "/*\nTheme Name: Client Child\nTemplate: flatsome\n*/\n",
        encoding="utf-8",
    )
    (child / "functions.php").write_text(
        str(synthetic_code_samples["theme_obfuscated_php"]),
        encoding="utf-8",
    )

    report = scan_child_theme(child)

    assert report.blocked is True
    assert any(finding.score >= 60 for finding in report.findings)


def test_child_theme_without_flatsome_template_is_blocked(tmp_path: Path):
    child = tmp_path / "other-child"
    child.mkdir()
    (child / "style.css").write_text(
        "/*\nTheme Name: Other Child\nTemplate: storefront\n*/\n",
        encoding="utf-8",
    )

    report = scan_child_theme(child)

    assert report.blocked is True
    assert any(finding.score >= 60 for finding in report.findings)


def test_flatsome_package_must_be_installable_theme_zip(tmp_path: Path):
    package = tmp_path / "flatsome.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "flatsome/style.css",
            "/*\nTheme Name: Flatsome\nVersion: 9.9\n*/\n",
        )
        archive.writestr("flatsome/functions.php", "<?php // trusted fixture\n")

    destination = tmp_path / "extract"
    destination.mkdir()
    root, digest = _safe_extract_flatsome(package, destination)

    assert root == destination
    assert (destination / "style.css").is_file()
    assert (destination / "functions.php").is_file()
    assert len(digest) == 64


def test_flatsome_package_can_have_extra_top_level_files_and_directories(tmp_path: Path):
    package = tmp_path / "flatsome-bundle.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("documentation/readme.txt", "Theme documentation")
        archive.writestr("license.txt", "License text")
        archive.writestr("__MACOSX/._junk", "metadata")
        archive.writestr(
            "theme-package/flatsome/style.css",
            "/*\nTheme Name: Flatsome\nVersion: 9.9\n*/\n",
        )
        archive.writestr("theme-package/flatsome/functions.php", "<?php // trusted fixture\n")
        archive.writestr("theme-package/flatsome/assets/app.css", "body{}")

    destination = tmp_path / "extract"
    root, digest = _safe_extract_flatsome(package, destination)

    assert root == destination
    assert (destination / "style.css").is_file()
    assert (destination / "functions.php").is_file()
    assert (destination / "assets" / "app.css").is_file()
    assert not (destination / "documentation").exists()
    assert not (destination / "license.txt").exists()
    assert not (destination / "__MACOSX").exists()
    assert len(digest) == 64
