from __future__ import annotations

from pathlib import Path

import pytest

from wpclean.fresh_install import FreshInstallRequest, build_wp_config, _database_create_bridge, _wordpress_install_bridge
from wpclean import gui_fresh_safe_entry  # noqa: F401 - activates full GUI + safety stack
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


def test_create_database_mode_requires_mysql_admin_credentials() -> None:
    with pytest.raises(ValueError, match="MySQL"):
        FreshInstallRequest.from_dict(_payload(dbMode="create"))

    req = FreshInstallRequest.from_dict(
        _payload(
            dbMode="create",
            mysqlAdminHost="localhost",
            mysqlAdminUser="rootish",
            mysqlAdminPassword="Mysql-Admin-Secret!",
            mysqlUserHost="localhost",
        )
    )
    assert req.db_mode == "create"


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


def test_gui_exposes_separate_fresh_install_wizard() -> None:
    html = render_app("test-token")

    assert "Cài WordPress mới" in html
    assert 'id="freshModal"' in html
    assert "Dùng database có sẵn" in html
    assert "Tạo database + user mới" in html
    assert "MySQL admin user" in html
    assert "Xóa toàn bộ dữ liệu hiện có" in html
    assert "/api/fresh-install" in html
    assert "freshProgress" in html


def test_launcher_points_to_safe_fresh_gui_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "giaodien.ps1").read_text(encoding="utf-8-sig")
    assert "python -m wpclean.gui_fresh_safe_entry" in script
    safety = (root / "src" / "wpclean" / "gui_fresh_safe_entry.py").read_text(encoding="utf-8")
    assert "Preflight database PASS" in safety
    assert "trước destructive boundary" in safety
