__version__ = "0.2.0"

# Register read-only final verification and recovery commands on the shared
# Typer app before the rebuild entry point is loaded.
from . import live_verify_command as _live_verify_command  # noqa: F401,E402
from . import mu_plugin_command as _mu_plugin_command  # noqa: F401,E402

# Harden destructive FTP wipe: if DELETE/RMD returns 550 Permission denied,
# try temporary SITE CHMOD recovery (up to 777 as last resort) and immediately
# retry deletion. This cannot bypass hosting ownership/ACL restrictions.
from . import permission_recovery as _permission_recovery  # noqa: F401,E402
