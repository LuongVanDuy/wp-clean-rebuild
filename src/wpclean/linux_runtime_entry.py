from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys

from .gui_runtime_entry import configure_console


def _open_repair_linux(name: str) -> str:
    """Open a theme repair workspace with the Ubuntu desktop file manager."""
    from . import gui_server

    payload = gui_server._project_payload(name)
    path = Path(str(payload.get("themeRepair") or ""))
    if not path.is_dir():
        raise FileNotFoundError("Dự án chưa có thư mục theme repair.")

    opener = shutil.which("xdg-open")
    if not opener:
        raise RuntimeError(
            "Không tìm thấy xdg-open. Hãy cài gói xdg-utils trên Ubuntu "
            "(sudo apt install xdg-utils), rồi thử lại."
        )

    try:
        subprocess.Popen(
            [opener, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Không thể mở thư mục repair: {exc}") from exc
    return str(path)


def configure_linux_gui() -> None:
    """Install Linux-only GUI integrations without changing the Windows path."""
    if os.name == "nt" or not sys.platform.startswith("linux"):
        return

    from . import gui_server

    gui_server.open_repair = _open_repair_linux


def main(*, stable: bool = False) -> None:
    configure_console()
    configure_linux_gui()

    if stable:
        from . import gui_ftp_logging_entry

        gui_ftp_logging_entry.main()
        return

    from . import gui_parallel_entry

    gui_parallel_entry.main()


if __name__ == "__main__":
    main(stable="--stable" in sys.argv[1:])
