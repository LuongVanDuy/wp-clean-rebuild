from pathlib import Path
import zipfile

from wpclean.backup import verify_manifest, write_manifest
from wpclean.scanners.sql import scan_sql
from wpclean.scanners.uploads import scan_uploads


def test_sql_compound_payload_is_flagged(tmp_path: Path):
    sql = tmp_path / "db.sql"
    sql.write_text("INSERT INTO wp_options VALUES (1,'x','eval(base64_decode(\"abc\"))','yes');\n", encoding="utf-8")
    findings = scan_sql(sql)
    assert len(findings) == 1
    assert findings[0].score >= 55


def test_php_hidden_as_image_is_critical(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "photo.jpg").write_bytes(b"\xff\xd8\xfffake-image<?php echo 'x';")
    findings = scan_uploads(uploads)
    assert findings
    assert findings[0].score >= 80
    assert findings[0].metadata["magic_type"] == "jpeg"
    assert findings[0].metadata["php_offset"] >= 0
    assert len(findings[0].metadata["sha256"]) == 64
    assert "<?php" in findings[0].preview


def test_bare_php_like_binary_bytes_do_not_flag_media(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "normal.webp").write_bytes(b"RIFFxxxxWEBP" + b"\x00" * 20 + b"<?" + b"\x88\x99" * 100)
    findings = scan_uploads(uploads)
    assert findings == []


def test_silence_is_golden_index_is_not_malware(tmp_path: Path):
    uploads = tmp_path / "uploads" / "wpseo-redirects"
    uploads.mkdir(parents=True)
    (uploads / "index.php").write_text("<?php\n// Silence is golden.\n", encoding="utf-8")
    findings = scan_uploads(tmp_path / "uploads")
    assert findings == []


def test_zip_with_php_entry_is_reviewed_without_scanning_compressed_bytes(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    archive_path = uploads / "archive.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin/test.php", "<?php echo 'x';")
    findings = scan_uploads(uploads)
    assert len(findings) == 1
    assert findings[0].score == 40
    assert findings[0].signals[0].name == "uploads.archive_executable"
    assert findings[0].metadata["archive_executable_entries"] == ["plugin/test.php"]
    assert len(findings[0].metadata["sha256"]) == 64


def test_manifest_detects_tamper(tmp_path: Path):
    (tmp_path / "a.txt").write_text("clean", encoding="utf-8")
    write_manifest(tmp_path)
    ok, problems = verify_manifest(tmp_path)
    assert ok
    assert problems == []
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    ok, problems = verify_manifest(tmp_path)
    assert not ok
    assert problems
