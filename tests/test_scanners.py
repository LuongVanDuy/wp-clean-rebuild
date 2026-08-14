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
    payload = b"\xff\xd8\xfffake-image" + b"<?php echo base64_decode($_POST['x']); ?>" + b"A" * 600
    (uploads / "photo.jpg").write_bytes(payload)
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


def test_random_short_echo_bytes_in_binary_media_do_not_flag(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    binary = b"\x89PNG\r\n\x1a\n" + (b"\xff\x80\x01\x9a" * 100) + b"<?=9\xff\x80garbage" + (b"\x81\xfe" * 300)
    (uploads / "normal.png").write_bytes(binary)
    findings = scan_uploads(uploads)
    assert findings == []


def test_textual_short_echo_payload_in_media_is_flagged(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    payload = b"RIFFxxxxWEBP" + b"A" * 128 + b"<?= $_GET['cmd']; ?>" + b" " * 600
    (uploads / "polyglot.webp").write_bytes(payload)
    findings = scan_uploads(uploads)
    assert len(findings) == 1
    assert findings[0].score >= 80


def test_silence_is_golden_index_is_not_malware(tmp_path: Path):
    uploads = tmp_path / "uploads" / "wpseo-redirects"
    uploads.mkdir(parents=True)
    (uploads / "index.php").write_text("<?php\n// Silence is golden.\n", encoding="utf-8")
    findings = scan_uploads(tmp_path / "uploads")
    assert findings == []


def test_zip_is_preserved_in_backup_but_dropped_from_clean_restore(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    archive_path = uploads / "archive.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("plugin/test.php", "<?php echo 'x';")
    findings = scan_uploads(uploads)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.score == 30
    assert finding.signals[0].name == "uploads.archive_restore_policy"
    assert finding.metadata["restore_policy"] == "drop"
    assert finding.metadata["archive_php_entries"] == ["plugin/test.php"]
    assert finding.recommended_action == "DROP FROM CLEAN RESTORE (ORIGINAL BACKUP KEPT)"
    assert len(finding.metadata["sha256"]) == 64
    assert archive_path.exists()


def test_zip_with_obfuscated_persistent_php_is_critical(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    archive_path = uploads / "malware.zip"
    malicious = b"""<?php
function abcdefghijklmnop($x){ return gzinflate(base64_decode($x)); }
function qwertyuiopasdfgh(){ wp_schedule_event(time()+300, 'hourly', 'x'); }
function zxcvbnmasdfghjkl(){ $u='bad'; if (username_exists($u)) { wp_set_password('x', username_exists($u)); } }
function poiuytrewqlkjhgf($p,$d){ file_put_contents($p,$d); chmod($p,0644); opcache_invalidate($p,true); }
function mnbvcxzlkjhgfds(){ return 1; }
function asdfghjklqwertyu(){ return 2; }
function qazwsxedcrfvtgby(){ return 3; }
function plmoknijbuhvygct(){ return 4; }
function lkjhgfdsaqwertyu(){ return 5; }
function ytrewqasdfghjklz(){ return 6; }
"""
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("payload/payload.php", malicious)

    findings = scan_uploads(uploads)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.score == 100
    assert any(signal.name == "uploads.archive_malicious_php" for signal in finding.signals)
    assert finding.metadata["restore_policy"] == "drop"
    assert finding.metadata["archive_suspicious_entries"][0]["entry"] == "payload/payload.php"
    assert finding.recommended_action == "QUARANTINE / DROP FROM CLEAN RESTORE (ORIGINAL BACKUP KEPT)"


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
