"""The commands the dashboard's Commands page offers.

``CATALOG`` describes each command the page lists: its label and purpose,
its fields, and for a command that removes something, what to confirm
first. The page renders it as served by ``GET /api/commands``, and
``POST /api/commands/{command}`` accepts exactly these commands plus the ones
the dashboard's own dialogs queue (``DIALOG_FLOWS``). ``queue.build_command_args`` turns a
submitted body into the CLI's arguments; every field key here is one of its
inputs.

The clients a ``client`` field offers come from the CLI
(``imprint clients --json``): its table is the one setup, enable, connect,
disable and disconnect resolve their targets through.

Left out on purpose (docs/queue.md): ``setup``, ``enable`` and ``profile``
install into the runtime the dashboard runs from, and ``disable``, ``ui``,
``server`` and ``uninstall`` stop or remove the dashboard itself; the page
connects and disconnects clients with ``connect`` and ``disconnect`` instead.
WSL profiles (Settings, ``wsl_profiles``), workspaces, purge, move, sync
pairing and updates have their own dashboard flows. The API queues
``bootstrap`` itself when Settings changes the embedding runtime (ADR 0062);
no client can request it.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys

CATALOG: tuple[dict, ...] = (
    {'name': 'learn', 'label': 'Learn conversations', 'group': 'Index',
     'description': 'Index coding-assistant conversations and memory files',
     'fields': [
         {'key': 'desktop', 'label': 'Desktop exports', 'kind': 'switch',
          'hint': 'Also index new Claude Desktop and ChatGPT export zips from Downloads'},
     ]},
    {'name': 'ingest', 'label': 'Ingest directory', 'group': 'Index',
     'description': 'Index a directory or file into memory',
     'fields': [
         {'key': 'dir', 'label': 'Directory', 'kind': 'path', 'browse': 'any', 'required': True},
         {'key': 'force', 'label': 'Re-index everything', 'kind': 'switch',
          'hint': 'Also re-embed files that have not changed'},
     ]},
    {'name': 'refresh', 'label': 'Refresh files', 'group': 'Index',
     'description': 'Re-index only files changed since the last run',
     'fields': [
         {'key': 'dir', 'label': 'Directory', 'kind': 'path', 'browse': 'dir', 'required': True},
     ]},
    {'name': 'ingest-url', 'label': 'Ingest URL', 'group': 'Index',
     'description': 'Fetch a page or file and index it',
     'fields': [
         {'key': 'url', 'label': 'URL', 'kind': 'text', 'placeholder': 'https://', 'required': True},
         {'key': 'project', 'label': 'Project', 'kind': 'text', 'placeholder': 'urls', 'suggest_projects': True},
         {'key': 'force', 'label': 'Fetch again', 'kind': 'switch',
          'hint': 'Re-index even when the URL has not changed'},
     ]},
    {'name': 'refresh-urls', 'label': 'Refresh URLs', 'group': 'Index',
     'description': 'Re-check stored URLs and re-index the changed ones', 'fields': []},
    {'name': 'retag', 'label': 'Retag memories', 'group': 'Maintain',
     'description': 'Tag stored chunks again with the configured tagger',
     'fields': [
         {'key': 'project', 'label': 'Project', 'kind': 'project'},
         {'key': 'all', 'label': 'Include tagged chunks', 'kind': 'switch',
          'hint': 'Also retag chunks the LLM tagger already handled'},
         {'key': 'dry_run', 'label': 'Dry run', 'kind': 'switch', 'hint': 'Tag without writing the results'},
         {'key': 'pending', 'label': 'Only memories waiting for tagging', 'kind': 'switch',
          'hint': 'Tag now, newest first, what learn and ingest left for background tagging'},
     ]},
    {'name': 'scrub', 'label': 'Scrub secrets', 'group': 'Maintain',
     'description': 'Find secrets stored in memories and facts; replace them with placeholders when applied',
     'fields': [
         {'key': 'apply', 'label': 'Redact them', 'kind': 'switch',
          'hint': 'Rewrite the memories and facts found and embed changed memories again; otherwise only count them'},
     ]},
    {'name': 'sync', 'label': 'Export snapshot', 'group': 'Maintain',
     'description': 'Write a sync bundle of the active workspace',
     'fields': [], 'fixed': {'action': 'export'}},
    {'name': 'connect', 'label': 'Connect clients', 'group': 'Clients',
     'description': "Register Imprint's MCP server, hooks and instructions with your AI tools",
     'fields': [{'key': 'target', 'label': 'Client', 'kind': 'client'}]},
    {'name': 'disconnect', 'label': 'Disconnect clients', 'group': 'Clients',
     'description': "Remove Imprint's MCP server, hooks and instructions from your AI tools",
     'fields': [{'key': 'target', 'label': 'Client', 'kind': 'client'}],
     'confirm': ("The chosen clients stop using Imprint: its MCP server, hooks and instructions are removed "
                 "from their settings, including their recorded WSL profiles. Memories, the runtime and this "
                 "dashboard stay; Connect clients restores the local ones.")},
    {'name': 'status', 'label': 'Status', 'group': 'Inspect',
     'description': 'Server state, memory counts and hooks', 'fields': []},
    {'name': 'doctor', 'label': 'Diagnose', 'group': 'Inspect',
     'description': 'Check the runtime, dependencies and integrations without changing them',
     'fields': [
         {'key': 'probe_mcp', 'label': 'Start the MCP server', 'kind': 'switch',
          'hint': "Also start Imprint's MCP server once and list its tools, as a client would"},
     ]},
    {'name': 'config', 'label': 'Show config', 'group': 'Inspect',
     'description': 'Every setting with its value and source', 'fields': []},
)

# Queued by the dashboard's own dialogs, which ask their own questions,
# rather than offered on the Commands page: Manage workspaces (wipe, migrate,
# workspace) and Settings' WSL profiles (wsl setup and remove).
DIALOG_FLOWS = frozenset({'wipe', 'migrate', 'workspace', 'wsl'})

ALLOWED = frozenset(command['name'] for command in CATALOG) | DIALOG_FLOWS


def clients() -> list[dict]:
    """The CLI's clients, each ``{name, label, connected}``."""
    from .queue import find_imprint_binary
    result = subprocess.run([find_imprint_binary(), 'clients', '--json'], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=15)
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-500:]
        raise RuntimeError(f'imprint clients failed (exit {result.returncode}): {detail}')
    return json.loads(result.stdout)


def catalog() -> dict:
    """What GET /api/commands serves: the catalog and its clients."""
    return {'commands': CATALOG, 'clients': clients()}


def wsl_profiles() -> dict:
    """What GET /api/wsl serves: ``imprint wsl list --json`` on a Windows
    runtime, which probes every distribution and so can start stopped ones.

    WSL profiles connect to a Windows installation (ADR 0026); any other
    runtime answers ``supported: false`` without running the CLI.
    """
    if sys.platform != 'win32':
        return {'supported': False, 'platform': platform.system()}
    from .queue import find_imprint_binary
    result = subprocess.run([find_imprint_binary(), 'wsl', 'list', '--json'], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=300)
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-500:]
        raise RuntimeError(f'imprint wsl list failed (exit {result.returncode}): {detail}')
    return {'supported': True, **json.loads(result.stdout)}
