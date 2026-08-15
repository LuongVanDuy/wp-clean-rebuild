from __future__ import annotations

import sys


def _configure_stream(stream) -> None:
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def configure_console() -> None:
    """Make GUI startup/output safe on legacy Windows console encodings."""
    seen: set[int] = set()
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        if stream is None or id(stream) in seen:
            continue
        seen.add(id(stream))
        _configure_stream(stream)


def main() -> None:
    configure_console()
    from . import gui_parallel_entry

    gui_parallel_entry.main()


if __name__ == "__main__":
    main()
