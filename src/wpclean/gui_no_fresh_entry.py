from __future__ import annotations

from . import gui_fresh_safe_entry  # noqa: F401 - keep Fresh Install code available but hidden
from . import gui_server as server
from . import gui_ui


_BASE_RENDER = gui_ui.render_app
_FRESH_INSTALL_BUTTON = '<button class="btn btn-success" onclick="openFreshInstall()">Cài WordPress mới</button>'


def _render_without_fresh_install(token: str) -> str:
    """Temporarily hide Fresh Install from the operator-facing GUI.

    The feature and backend remain intact so it can be re-enabled later without
    restoring code. Only the normal GUI launch control is removed.
    """
    html = _BASE_RENDER(token)
    return html.replace(_FRESH_INSTALL_BUTTON, "", 1)


gui_ui.render_app = _render_without_fresh_install


def main() -> None:
    server.main()


if __name__ == "__main__":
    main()
