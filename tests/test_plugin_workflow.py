from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wpclean.rebuild_entry import app
import wpclean.plugin_workflow as workflow
from wpclean.plugin_restore import (
    PluginClassification,
    PluginInstallResult,
    WordPressOrgPlugin,
)


class DummyProfile:
    host = "example.com"
    remote_path = "/public_html"
    workers = 4


def _write_plugin(backup_root: Path, slug: str, name: str) -> None:
    root = backup_root / "plugins" / slug
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{slug}.php").write_text(
        f"<?php\n/**\n * Plugin Name: {name}\n * Version: 1.0\n */\n",
        encoding="utf-8",
    )


def test_cli_registers_plugin_only_recovery_command():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "rebuild-plugin-config" in result.stdout


def test_manual_plugin_is_reported_but_never_uploaded(tmp_path: Path, monkeypatch):
    backup_root = tmp_path / "backup"
    _write_plugin(backup_root, "premium-private", "Premium Private")

    def fake_classify(inventory, **kwargs):
        return [
            PluginClassification(
                inventory=inventory[0],
                status="manual",
                detail="Not found in the WordPress.org Plugin Directory.",
            )
        ]

    monkeypatch.setattr(workflow, "classify_plugins", fake_classify)

    def fail_install(*args, **kwargs):
        raise AssertionError("private plugin backup must never be uploaded")

    monkeypatch.setattr(workflow, "install_wordpress_org_plugin", fail_install)

    stage = workflow.run_plugin_stage(
        profile=DummyProfile(),
        transport=object(),
        backup_root=backup_root,
        report_path=tmp_path / "reports" / "rebuild-execute.json",
    )

    assert stage.manual_count == 1
    assert stage.wordpress_org_count == 0
    assert stage.installed_count == 0


def test_public_plugin_uses_fresh_wordpress_org_install(tmp_path: Path, monkeypatch):
    backup_root = tmp_path / "backup"
    _write_plugin(backup_root, "contact-form-7", "Contact Form 7")

    def fake_classify(inventory, **kwargs):
        return [
            PluginClassification(
                inventory=inventory[0],
                status="wordpress.org",
                wporg=WordPressOrgPlugin(
                    requested_slug="contact-form-7",
                    slug="contact-form-7",
                    name="Contact Form 7",
                    version="6.0",
                    download_link="https://downloads.wordpress.org/plugin/contact-form-7.6.0.zip",
                ),
            )
        ]

    monkeypatch.setattr(workflow, "classify_plugins", fake_classify)
    monkeypatch.setattr(workflow.typer, "confirm", lambda *args, **kwargs: True)
    calls: list[str] = []

    def fake_install(profile, transport, classification, **kwargs):
        calls.append(classification.inventory.slug)
        return PluginInstallResult(
            source_slug="contact-form-7",
            wporg_slug="contact-form-7",
            name="Contact Form 7",
            version="6.0",
            remote_slug="contact-form-7",
            files_uploaded=25,
            package_sha256="a" * 64,
            download_link="https://downloads.wordpress.org/plugin/contact-form-7.6.0.zip",
        )

    monkeypatch.setattr(workflow, "install_wordpress_org_plugin", fake_install)

    stage = workflow.run_plugin_stage(
        profile=DummyProfile(),
        transport=object(),
        backup_root=backup_root,
        report_path=tmp_path / "reports" / "rebuild-execute.json",
    )

    assert calls == ["contact-form-7"]
    assert stage.wordpress_org_count == 1
    assert stage.install_accepted is True
    assert stage.installed_count == 1
