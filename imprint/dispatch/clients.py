"""Which clients the Dispatcher can start on this device (ADR 0222).

A client is available when its binary is found, its ``--help`` lists the
safety flags its runner passes (an older version is refused rather than
run with less safety), and Imprint is connected to it, so the session can
take its claim over. Every available client is allowed until the person
unticks it (``dispatch.excluded_clients``); the default client is
``dispatch.default_client``, else the first allowed one in the runners'
order. The check runs when the dashboard server starts, every hour, and
when the Board's strip asks for it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

from . import runners

# Clients with a one-shot mode but no sandbox and no check of each action:
# listed when installed, never started (ADR 0222).
UNSAFE = (('opencode', 'OpenCode', 'opencode'), ('cline', 'Cline', 'cline'), ('openclaw', 'OpenClaw', 'openclaw'))
CHECK_EVERY = 3600.0
TIMEOUT = 15
_cache: dict = {}
_lock = threading.Lock()


def _setting(key: str) -> str:
    from ..config_schema import resolve
    return str(resolve(key)[0] or '').strip()


def excluded() -> set[str]:
    return {name.strip() for name in _setting('dispatch.excluded_clients').split(',') if name.strip()}


def _run(argv: list[str]) -> str:
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    done = subprocess.run(argv, capture_output=True, timeout=TIMEOUT, check=False, stdin=subprocess.DEVNULL, **options)
    return (done.stdout + done.stderr).decode('utf-8', 'replace')


def connected(runner: runners.Runner) -> bool:
    """Whether the client's own configuration starts Imprint's MCP server."""
    try:
        if runner.client == 'claude-code':
            home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home())
            for path in (home / '.claude.json', Path.home() / '.claude.json'):
                if path.is_file():
                    servers = json.loads(path.read_text(encoding='utf-8')).get('mcpServers') or {}
                    if 'imprint' in servers:
                        return True
            return False
        if runner.client == 'codex':
            path = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'config.toml'
            return path.is_file() and 'imprint' in (tomllib.loads(path.read_text(encoding='utf-8')).get('mcp_servers') or {})
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return False
    return False


def detect(runner: runners.Runner) -> dict:
    """``{'client', 'label', 'available', 'version', 'binary', 'reason'}`` for one client here."""
    found = {'client': runner.client, 'label': runner.label, 'available': False, 'version': None, 'binary': None,
             'reason': None}
    binary = runners.find(runner)
    if not binary:
        return {**found, 'reason': f'not installed: no {runner.binary} on PATH or in VS Code, and {runner.setting} is not set'}
    found['binary'] = binary
    try:
        found['version'] = (_run([binary, '--version']).strip().splitlines() or [''])[0][:80] or None
        text = _run([binary, *runner.help_args])
    except (OSError, subprocess.SubprocessError) as error:
        return {**found, 'reason': f'{binary} did not run: {error}'}
    missing = [flag for flag in runner.required if flag not in text]
    if missing:
        return {**found, 'reason': f'this version lacks {", ".join(missing)}, which Dispatched sessions need: update it'}
    if not connected(runner):
        return {**found, 'reason': f'Imprint is not connected to {runner.label}: run `imprint connect {runner.client}`'}
    return {**found, 'available': True}


def detected(*, refresh: bool = False) -> list[dict]:
    """Every runner's client here, in the runners' order, checked at most hourly unless ``refresh``."""
    with _lock:
        if refresh or time.monotonic() - _cache.get('at', -CHECK_EVERY) >= CHECK_EVERY:
            found = [detect(runner) for runner in runners.RUNNERS.values()]
            found += [{'client': client, 'label': label, 'available': False, 'version': None, 'binary': binary,
                       'reason': 'installed, but it has no safe unattended mode (no sandbox and no check of each action), '
                                 'so the Dispatcher never starts it'}
                      for client, label, name in UNSAFE if (binary := shutil.which(name))]
            _cache.update(at=time.monotonic(), found=found)
        return [dict(found) for found in _cache['found']]


def allowed(*, refresh: bool = False) -> list[str]:
    """The available clients the person has not unticked, in the runners' order."""
    off = excluded()
    return [found['client'] for found in detected(refresh=refresh) if found['available'] and found['client'] not in off]


def default(choices: list[str] | None = None) -> str | None:
    """The client for a Task assigned to none: the person's choice when it is allowed, else the first allowed."""
    choices = allowed() if choices is None else choices
    chosen = _setting('dispatch.default_client')
    return chosen if chosen in choices else (choices[0] if choices else None)


def listing(*, refresh: bool = False) -> list[dict]:
    """The clients as the Board's strip shows them, with ``allowed`` and ``default``."""
    off = excluded()
    found = detected(refresh=refresh)
    choices = [f['client'] for f in found if f['available'] and f['client'] not in off]
    chosen = default(choices)
    return [{**f, 'allowed': f['available'] and f['client'] not in off, 'default': f['client'] == chosen} for f in found]


def for_task(state: dict, choices: list[str], device: str) -> str | None:
    """Which client works ``state``: a client it is assigned to (alone, or
    on this device) when allowed, else the default client; None when it is
    assigned only to clients that are not allowed here."""
    named = []
    for assignee in state.get('assignees') or []:
        client, _, on = assignee.partition('@')
        if client and client in runners.RUNNERS and (not on or on.lower() == device.lower()):
            named.append(client)
        elif client and client not in runners.RUNNERS and client not in ('user',):
            named.append(client)  # a client no runner starts: never the default instead
    if named:
        return next((client for client in named if client in choices), None)
    return default(choices)
