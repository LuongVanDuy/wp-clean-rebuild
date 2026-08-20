from pathlib import Path


def test_ubuntu_installer_bootstraps_uv_python_and_dependencies():
    script = Path("install.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "https://astral.sh/uv/install.sh" in script
    assert '"$UV_EXE" python find 3.13' in script
    assert '"$UV_EXE" python install 3.13' in script
    assert '"$UV_EXE" sync' in script
    assert '"$UV_EXE" run --no-sync wpclean doctor' in script


def test_ubuntu_gui_launcher_uses_linux_runtime_and_stable_fallback():
    script = Path("START.sh").read_text(encoding="utf-8")

    assert "PYTHONUTF8=1" in script
    assert "wpclean.linux_runtime_entry" in script
    assert "wpclean.linux_runtime_entry --stable" in script
    assert "gui-startup-linux.log" in script


def test_ubuntu_cli_wrapper_matches_windows_cli_entrypoint():
    script = Path("wpclean.sh").read_text(encoding="utf-8")

    assert 'exec "$UV_EXE" run --no-sync wpclean "$@"' in script


def test_linux_runtime_uses_xdg_open_for_repair_workspace():
    script = Path("src/wpclean/linux_runtime_entry.py").read_text(encoding="utf-8")

    assert 'shutil.which("xdg-open")' in script
    assert "subprocess.Popen(" in script
    assert "gui_server.open_repair = _open_repair_linux" in script
