from __future__ import annotations

import json
import re
from pathlib import Path

from wpclean.backup import write_manifest
from wpclean.clean_builder import build_clean_restore, wordpress_password_hash


SQL_FIXTURE = """-- WP Clean Rebuild database backup
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS=0;

DROP TABLE IF EXISTS `wp_users`;
CREATE TABLE `wp_users` (
  `ID` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_login` varchar(60) NOT NULL DEFAULT '',
  `user_pass` varchar(255) NOT NULL DEFAULT '',
  `user_nicename` varchar(50) NOT NULL DEFAULT '',
  `user_email` varchar(100) NOT NULL DEFAULT '',
  `user_url` varchar(100) NOT NULL DEFAULT '',
  `user_registered` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `user_activation_key` varchar(255) NOT NULL DEFAULT '',
  `user_status` int NOT NULL DEFAULT 0,
  `display_name` varchar(250) NOT NULL DEFAULT '',
  PRIMARY KEY (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `wp_users` VALUES ('1','oldadmin','oldhash','oldadmin','old@example.test','','2020-01-01 00:00:00','','0','Old Admin'),
('2','attacker','evilhash','attacker','evil@example.test','','2026-01-01 00:00:00','','0','Attacker');

DROP TABLE IF EXISTS `wp_usermeta`;
CREATE TABLE `wp_usermeta` (
  `umeta_id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_id` bigint unsigned NOT NULL DEFAULT 0,
  `meta_key` varchar(255) DEFAULT NULL,
  `meta_value` longtext,
  PRIMARY KEY (`umeta_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `wp_usermeta` VALUES ('1','1','wp_capabilities','a:1:{s:13:\"administrator\";b:1;}'),
('2','2','wp_capabilities','a:1:{s:13:\"administrator\";b:1;}');

DROP TABLE IF EXISTS `wp_options`;
CREATE TABLE `wp_options` (`option_id` bigint unsigned NOT NULL AUTO_INCREMENT, `option_name` varchar(191) NOT NULL, `option_value` longtext NOT NULL, `autoload` varchar(20) NOT NULL DEFAULT 'yes', PRIMARY KEY (`option_id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS `wp_posts`;
CREATE TABLE `wp_posts` (`ID` bigint unsigned NOT NULL AUTO_INCREMENT, `post_author` bigint unsigned NOT NULL DEFAULT 0, PRIMARY KEY (`ID`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `wp_posts` VALUES ('10','2');

DROP TABLE IF EXISTS `wp_comments`;
CREATE TABLE `wp_comments` (`comment_ID` bigint unsigned NOT NULL AUTO_INCREMENT, `user_id` bigint unsigned NOT NULL DEFAULT 0, PRIMARY KEY (`comment_ID`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `wp_comments` VALUES ('11','2');

SET FOREIGN_KEY_CHECKS=1;
"""


def _make_verified_backup(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "database").mkdir(parents=True)
    (root / "uploads").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    (root / "database" / "original.sql").write_text(SQL_FIXTURE, encoding="utf-8")
    (root / "uploads" / "photo.jpg").write_bytes(b"\xff\xd8\xffclean-image")
    (root / "uploads" / "payload.zip").write_bytes(b"PK\x03\x04not-needed-for-restore")
    (root / "config" / "wp-config.php").write_text("<?php // backup evidence", encoding="utf-8")
    (root / "backup-report.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    write_manifest(root)
    return root


def test_wordpress_hash_does_not_contain_plaintext() -> None:
    password = "FTP-secret-123!"
    hashed = wordpress_password_hash(password)
    assert hashed.startswith("$wp$2")
    assert password not in hashed


def test_build_clean_restore_drops_zip_and_resets_all_users(tmp_path: Path) -> None:
    root = _make_verified_backup(tmp_path)
    ftp_password = "FTP-secret-123!"

    report = build_clean_restore(
        root,
        ftp_password=ftp_password,
        host="example.test",
    )

    assert (root / "uploads" / "payload.zip").exists(), "original evidence must remain untouched"
    assert (root / "clean" / "uploads" / "photo.jpg").exists()
    assert not (root / "clean" / "uploads" / "payload.zip").exists()
    assert report.uploads_dropped == 1
    assert report.clean_verified is True

    clean_sql = (root / "clean" / "database" / "clean.sql").read_text(encoding="utf-8")
    assert "oldadmin" not in clean_sql
    assert "attacker" not in clean_sql
    assert "evilhash" not in clean_sql
    assert ftp_password not in clean_sql
    assert "INSERT INTO `wp_users` VALUES ('1','admin'" in clean_sql
    assert "wp_capabilities" in clean_sql
    assert "administrator" in clean_sql
    assert "UPDATE `wp_posts` SET `post_author`=1" in clean_sql
    assert "UPDATE `wp_comments` SET `user_id`=0" in clean_sql

    hashes = re.findall(r"\$wp\$2[aby]\$[A-Za-z0-9./$]+", clean_sql)
    assert hashes, "clean SQL should contain a WordPress-prefixed bcrypt hash"
