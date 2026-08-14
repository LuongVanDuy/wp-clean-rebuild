from __future__ import annotations

from pathlib import Path

import pytest

from wpclean.fresh_install import FreshInstallRequest, build_wp_config, _database_create_bridge, _wordpress_install_bridge
from wpclean import gui_fresh_safe_entry
from wpclean.gui_ui import render_app


def _payload(**overrides):
    data = {
        "projectName": "fresh-site",
        "siteUrl": "https://fresh.test",
        "siteTitle": "Fresh Site",
        "adminUser": "site-admin",
        "adminPassword": "Wp-Admin-Secret-123!",
        "adminEmail": "admin@fresh.test",
        "tablePrefix": "wp_",
        "ftpHost": "fresh.test",
        "ftpUsername": "ftp-user",
        "ftpPassword": "Ftp-Secret-123!",
        "ftpProtocol": "ftp",
        "ftpPort": 21,
        "remotePath": "/domains/fresh.test/public_html",
        "dbMode": "existing",
        "dbHost": "localhost",
        "dbName": "fresh_wp",
        "dbUser": "fresh_wp",
        "dbPassword": "Db-Secret-123!",
    }
    data.update(overrides)
    return data


def test_fresh_request_requires_exact_domain_before_wipe() -> None:
    with pytest.raises(ValueError, match="nhập chính xác domain"):
        FreshInstallRequest.from_dict(_payload(wipeExisting=True, confirmText="wrong.test"))

    req = FreshInstallRequest.from_dict(_payload(wipeExisting=True, confirmText="fresh.test"))
    assert req.wipe_existing is True
    assert req.site_host == "fresh.test"


def test_gui_create_database_mode_can_fallback_to_database_user_credentials() -> None:
    # Core request still expects explicit creator credentials. The GUI safety layer
    # normalizes empty MySQL-admin fields to DB user/password so it can try the
    # common "same MySQL account has CREATE DATABASE" hosting configuration.
    with pytest.raises(ValueError, match="MySQL"):
        FreshInstallRequest.from_dict(_payload(dbMode="create"))

    normalized = gui_fresh_safe_entry._normalize_fresh_request_data(_payload(dbMode="create"))
    assert normalized["mysqlAdminHost"] == "localhost"
    assert normalized["mysqlAdminUser"] == "fresh_wp"
    assert normalized["mysqlAdminPassword"] == "Db-Secret-123!"

    req = FreshInstallRequest.from_dict(normalized)
    assert req.db_mode == "create"
    assert gui_fresh_safe_entry._using_database_user_as_creator(req) is True


def test_gui_create_database_mode_rejects_half_filled_mysql_admin() -> None:
    with pytest.raises(ValueError, match="nhập đủ cả username và password"):
        gui_fresh_safe_entry._normalize_fresh_request_data(
            _payload(dbMode="create", mysqlAdminUser="rootish", mysqlAdminPassword="")
        )

    explicit = gui_fresh_safe_entry._normalize_fresh_request_data(
        _payload(
            dbMode="create",
            mysqlAdminHost="localhost",
            mysqlAdminUser="rootish",
            mysqlAdminPassword="Mysql-Admin-Secret!",
            mysqlUserHost="localhost",
        )
    )
    assert explicit["mysqlAdminUser"] == "rootish"
    assert explicit["mysqlAdminPassword"] == "Mysql-Admin-Secret!"


def test_fresh_wp_config_uses_new_database_and_never_contains_wp_admin_secret() -> None:
    req = FreshInstallRequest.from_dict(_payload())
    config = build_wp_config(req)

    assert "define('DB_NAME', 'fresh_wp')" in config
    assert "define('DB_USER', 'fresh_wp')" in config
    assert "Db-Secret-123!" in config
    assert "$table_prefix = 'wp_'" in config
    assert "DISALLOW_FILE_EDIT" in config
    assert "Wp-Admin-Secret-123!" not in config
    assert "Ftp-Secret-123!" not in config


def test_temporary_php_bridges_do_not_embed_plaintext_secrets() -> None:
    req = FreshInstallRequest.from_dict(
        _payload(
            dbMode="create",
            mysqlAdminUser="mysql-admin",
            mysqlAdminPassword="Mysql-Admin-Secret!",
        )
    )
    db_bridge = _database_create_bridge(req, "token-123")
    install_bridge = _wordpress_install_bridge(req, "token-456")

    assert "Mysql-Admin-Secret!" not in db_bridge
    assert "Db-Secret-123!" not in db_bridge
    assert "Wp-Admin-Secret-123!" not in install_bridge
    assert "base64_decode" in db_bridge
    assert "wp_install" in install_bridge


def test_safe_entry_translates_dot_prefixed_bridges_for_http_compatibility() -> None:
    assert gui_fresh_safe_entry._visible_bridge_name(".wpclean-db-abc.php") == "wpclean-db-abc.php"
    assert gui_fresh_safe_entry._visible_bridge_name("/public_html/.wpclean-install-abc.php") == "/public_html/wpclean-install-abc.php"
    assert gui_fresh_safe_entry._visible_bridge_name("/public_html/index.php") == "/public_html/index.php"


def test_database_preflight_requires_empty_and_writable_target_database() -> None:
    req = FreshInstallRequest.from_dict(_payload())
    php = gui_fresh_safe_entry._database_test_bridge(req, "token-test")
    assert "SHOW TABLES" in php
    assert "Database is not empty" in php
    assert "CREATE TABLE" in php
    assert "INSERT INTO" in php
    assert "DROP TABLE" in php
    assert "tables'=>0" in php


def test_gui_exposes_fresh_install_with_visible_footer_and_optional_mysql_admin() -> None:
    html = render_app("test-token")

    assert "Cài WordPress mới" in html
    assert 'id="freshModal"' in html
    assert "Dùng database có sẵn" in html
    assert "Tạo database tự động qua PHP" in html
    assert "MySQL admin user (tùy chọn)" in html
    assert "Có thể để trống MySQL admin" in html
    assert "Database user + Database password" in html
    assert "Xóa toàn bộ dữ liệu hiện có" in html
    assert "/api/fresh-install" in html
    assert "freshProgress" in html
    assert "bottom:0!important" in html
    assert "Sửa thông tin & thử lại" in html


def test_launcher_points_to_safe_fresh_gui_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "giaodien.ps1").read_text(encoding="utf-8-sig")
    assert "python -m wpclean.gui_fresh_safe_entry" in script
    safety = (root / "src" / "wpclean" / "gui_fresh_safe_entry.py").read_text(encoding="utf-8")
    assert "Preflight database PASS" in safety
    assert "trước destructive boundary" in safety
    assert "does not create a normal Clean/Rebuild project" in safety
    assert "Thư mục đích không rỗng" in safety
    assert "Đang chạy Fresh Install" in safety
    assert "Hoàn tất Fresh Install" in safety
    assert "base._save_profile_after_install" not in safety
    assert "_normalize_fresh_request_data" in safety
