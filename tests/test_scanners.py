from pathlib import Path

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
    (uploads / "photo.jpg").write_bytes(b"<?php echo 'x';")
    findings = scan_uploads(uploads)
    assert findings
    assert findings[0].score >= 80


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
