import os
from . import config

# A running client session must not follow another window's workspace switch.
if not os.environ.get('IMPRINT_WORKSPACE'):
    os.environ['IMPRINT_WORKSPACE'] = config.get_active_workspace()

from .server import mcp
from .device import current
from .wsl_paths import client_context
import json

mcp.instructions = (mcp.instructions or '') + '\n' + json.dumps({
    'current_device': current(), 'working_directory': os.getcwd(),
    'path_guidance': 'Shared memories and conversations are portable. Original paths are provenance, not local file instructions. Use device_context and project mappings; only local_source_path is a local hint, and verify live files before edits.',
    **client_context()})

mcp.run()
