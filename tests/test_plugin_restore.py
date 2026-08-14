from __future__ import annotations

from pathlib import Path
import io
import zipfile

import wpclean.plugin_restore as plugin_restore
from wpclean.plugin_restore import (
    PluginInventoryItem,
    WordPressOrgPlugin,
    classify_plugin,
    inventory_backup_plugins,
    _extract_plugin_package,
)


def _write_plugin(root: Path, slug: str, *, name: str, uri: str = "", text_domain: str = "") -> Path:
    plugin = root / "plugins" / slug
    plugin.mkdir(parents=True, exist_ok=True)
    header = ["<?php", "/**", f" * Plugin Name: {name}", " * Version: 1.2.3"]
    if uri:
        header.append(f" * Plugin URI: {uri}")
    if text_domain:
        header.append(f" * Text Domain: {text_domain}")
    header.extend([" */", ""])
    (plugin / f"{slug}.php").write_text("\n".join(header), encoding="utf-8")
    return plugin


def test_inventory_reads_directory_plugins_and_header_candidates(tmp_path: Path):
    _write_plugin(
        tmp_path,
        "renamed-cache",
        name="Example Cache",
        uri="https://wordpress.org/plugins/example-cache/",
        text_domain="example-cache",
    )
    (tmp_path / "plugins" / "index.php").write_text("<?php", encoding="utf-8")

    items = inventory_backup_plugins(tmp_path)

    assert len(items) == 1
    item = items[0]
    assert item.slug == "renamed-cache"
    assert item.name == "Example Cache"
    assert item.version == "1.2.3"
    assert item.main_file == "renamed-cache/renamed-cache.php"
    assert item.candidate_slugs == ["renamed-cache", "example-cache"]


def test_public_plugin_is_selected_from_wordpress_org(monkeypatch):
    item = PluginInventoryItem(
        slug="renamed-cache",
        source_path="backup/plugins/renamed-cache",
        kind="directory",
        name="Example Cache",
        candidate_slugs=["renamed-cache", "example-cache"],
    )
    calls: list[str] = []

    def fake_info(slug: str):
        calls.append(slug)
        if slug == "renamed-cache":
            return None
        return WordPressOrgPlugin(
            requested_slug=slug,
            slug="example-cache",
            name="Example Cache",
            version="9.0",
            download_link="https://downloads.wordpress.org/plugin/example-cache.9.0.zip",
        )

    monkeypatch.setattr(plugin_restore, "wordpress_org_plugin_info", fake_info)

    result = classify_plugin(item)

    assert result.status == "wordpress.org"
    assert result.wporg is not None
    assert result.wporg.slug == "example-cache"
    assert calls == ["renamed-cache", "example-cache"]


def test_private_plugin_is_manual_not_restored_from_backup(monkeypatch):
    item = PluginInventoryItem(
        slug="premium-private",
        source_path="backup/plugins/premium-private",
        kind="directory",
        name="Premium Private",
        candidate_slugs=["premium-private"],
    )
    monkeypatch.setattr(plugin_restore, "wordpress_org_plugin_info", lambda slug: None)

    result = classify_plugin(item)

    assert result.status == "manual"
    assert result.wporg is None
    assert "WordPress.org" in result.detail


def test_api_failure_is_not_misclassified_as_private(monkeypatch):
    item = PluginInventoryItem(
        slug="some-plugin",
        source_path="backup/plugins/some-plugin",
        kind="directory",
        candidate_slugs=["some-plugin"],
    )

    def fail(_slug: str):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(plugin_restore, "wordpress_org_plugin_info", fail)

    result = classify_plugin(item)

    assert result.status == "lookup-error"
    assert "network unavailable" in result.detail


def test_official_plugin_zip_extracts_and_validates_main_header(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "example-cache/example-cache.php",
            "<?php\n/**\n * Plugin Name: Example Cache\n * Version: 9.0\n */\n",
        )
        archive.writestr("example-cache/assets/app.js", "console.log('ok');")

    info = WordPressOrgPlugin(
        requested_slug="example-cache",
        slug="example-cache",
        name="Example Cache",
        version="9.0",
        download_link="https://downloads.wordpress.org/plugin/example-cache.9.0.zip",
    )
    destination = tmp_path / "extract"

    root = _extract_plugin_package(buffer.getvalue(), destination, info)

    assert root == destination / "example-cache"
    assert (root / "example-cache.php").is_file()
    assert (root / "assets" / "app.js").is_file()


def test_plugin_zip_path_traversal_is_blocked(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../evil.php", "<?php")
        archive.writestr(
            "example-cache/example-cache.php",
            "<?php\n/** Plugin Name: Example Cache */\n",
        )

    info = WordPressOrgPlugin(
        requested_slug="example-cache",
        slug="example-cache",
        name="Example Cache",
        version="9.0",
        download_link="https://downloads.wordpress.org/plugin/example-cache.9.0.zip",
    )

    try:
        _extract_plugin_package(buffer.getvalue(), tmp_path / "extract", info)
    except RuntimeError as exc:
        assert "Unsafe path" in str(exc)
    else:
        raise AssertionError("path traversal ZIP must be blocked")
