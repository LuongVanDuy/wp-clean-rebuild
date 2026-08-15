from __future__ import annotations

from .gui_runtime_entry import configure_console


def main() -> None:
    configure_console()
    from . import gui_ftp_logging_entry

    gui_ftp_logging_entry.main()


if __name__ == "__main__":
    main()
