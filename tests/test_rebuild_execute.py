from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import re
import zipfile

from typer.testing import CliRunner

from wpclean.rebuild_entry import app
from wpclean.rebuild_execute import (
    SALT_KEYS,
    _database_import_bridge,
    _extract_wordpress,
    build_fresh_wp_config,
)


def test_fresh_wp_config_preserves_db_prefix_and_rotates_salts(tmp_path: Path):
    source = tmp_path / "wp-config.php"
    source.write_text(
        "<?php\n"
        "define('DB_NAME', 'example_db');\n"
        "define('DB_USER', 'example_user');\n"
        "define('DB_PASSWORD', 'db-secret');\n"
        "define('DB_HOST', 'localhost');\n"
        "define('DB_CHARSET', 'utf8mb4');\n"
        "define('DB_COLLATE', '');\n"
        "define('AUTH_KEY', 'old-compromised-salt');\n"
        "$table_prefix = 'old_';\n",
        encoding="utf-8",
    )

    generated = build_fresh_wp_config(source, table_prefix="dawtt_")

    assert "define('DB_NAME', 'example_db');" in generated
    assert "define('DB_USER', 'example_user');" in generated
    assert "define('DB_PASSWORD', 'db-secret');" in generated
    assert "$table_prefix = 'dawtt_';" in generated
    assert "old-compromised-salt" not in generated
    assert "DISALLOW_FILE_EDIT" in generated
    for key in SALT_KEYS:
        match = re.search(rf"define\('{key}', '([^']+)'\);", generated)
        assert match is not None
        assert len(match.group(1)) >= 64


def test_extract_wordpress_requires_expected_layout_and_reads_version(tmp_path: Path):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "wordpress/wp-includes/version.php",
            "<?php\n$wp_version = '9.9-test';\n",
        )
        archive.writestr("wordpress/index.php", "<?php echo 'ok';")

    version = _extract_wordpress(buffer.getvalue(), tmp_path / "core")

    assert version == "9.9-test"
    assert (tmp_path / "core" / "index.php").is_file()


def test_database_import_bridge_uses_header_token_not_query_string():
    bridge = _database_import_bridge("abc123", "payload.dat")
    assert "HTTP_X_WPCLEAN_TOKEN" in bridge
    assert "$_GET['token']" not in bridge
    assert "payload.dat" in bridge
    assert "hash_equals" in bridge


def test_rebuild_command_requires_explicit_execute_flag(tmp_path: Path):
    profile = tmp_path / "ftp.json"
    profile.write_text(
        json.dumps(
            {
                "host": "example.com",
                "username": "ftp-user",
                "password": "ftp-pass",
                "protocol": "ftp",
                "port": 21,
                "remotePath": "/domains/example.com/public_html",
                "siteUrl": "https://example.com",
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["rebuild-config", str(profile)])

    assert result.exit_code == 0
    assert "DRY ARM ONLY" in result.stdout
    assert "--execute" in result.stdout
    assert "nothing was changed remotely" in result.stdout.lower()
