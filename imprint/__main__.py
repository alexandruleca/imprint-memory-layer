import os
from . import config

# A running client session must not follow another window's workspace switch.
if not os.environ.get('IMPRINT_WORKSPACE'):
    os.environ['IMPRINT_WORKSPACE'] = config.get_active_workspace()

from .server import mcp

mcp.run()
