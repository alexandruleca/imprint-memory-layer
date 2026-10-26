import os

# Pin this process's workspace for the session it serves: the directory's
# workspace (ADR 0072), so another window's switch cannot redirect it.
from .server import mcp, start_session
start_session(os.getcwd())

from .device import current
from .wsl_paths import client_context
import json

mcp.instructions = (mcp.instructions or '') + '\n' + json.dumps({
    'current_device': current(), 'working_directory': os.getcwd(),
    'path_guidance': 'Shared memories and conversations are portable. Original paths are provenance, not local file instructions. Use device_context and project mappings; only local_source_path is a local hint, and verify live files before edits.',
    **client_context()})

mcp.run()
