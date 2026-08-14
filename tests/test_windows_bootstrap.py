from pathlib import Path


def test_batdau_uses_non_terminating_native_probe_for_fresh_windows_machine():
    script = Path("batdau.ps1").read_text(encoding="utf-8-sig")

    assert "$ErrorActionPreference = 'Stop'" in script
    assert "function KiemTra-LenhNative" in script
    assert "$ErrorActionPreference = 'SilentlyContinue'" in script
    assert "KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')" in script
    assert "Bạn có muốn tải và cài Python 3.13 tự động bằng uv không?" in script
    assert "& $uvExe python install 3.13" in script
    assert script.count("KiemTra-LenhNative -FilePath $uvExe -Arguments @('python', 'find', '3.13')") >= 2


def test_batdau_doctor_probe_is_also_non_terminating():
    script = Path("batdau.ps1").read_text(encoding="utf-8-sig")

    probe = "KiemTra-LenhNative -FilePath $uvExe -Arguments @('run', '--no-sync', 'wpclean', 'doctor')"
    assert script.count(probe) >= 2
    assert "Bạn có muốn cài/đồng bộ thư viện dự án tự động không?" in script
