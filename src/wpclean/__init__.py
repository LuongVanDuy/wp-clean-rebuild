__version__ = "0.2.0"

# Register read-only final verification on the shared Typer app before the
# rebuild entry point is loaded. The command module imports wpclean.cli.app,
# so this does not mutate or duplicate the CLI object.
from . import live_verify_command as _live_verify_command  # noqa: F401,E402
from . import mu_plugin_command as _mu_plugin_command  # noqa: F401,E402
