"""How each client is started for a Dispatched session (ADR 0222).

A client is started only through a one-shot mode that has an OS sandbox or
a check of each action, and never with one of its bypass flags. Each
runner says how to find the client's binary and builds its exact command
line; every command line is checked against the refused flags before it
runs.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

# Flags and values that switch a client's safety off (ADR 0222's table). No
# runner passes them; `check` refuses a command line that contains one.
REFUSED = frozenset({
    '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', 'bypassPermissions',
    '--dangerously-bypass-approvals-and-sandbox', '--dangerously-bypass-hook-trust', 'danger-full-access',
    '--yolo', '--force', '-f', '--allow-all', '--allow-all-tools', '--allow-all-paths', '--allow-all-urls',
    '--auto',
})
# Variables that do the same.
REFUSED_ENV = frozenset({'COPILOT_ALLOW_ALL'})


@dataclass(frozen=True)
class Runner:
    client: str     # as the Board names it: "claude-code"
    label: str      # as people read it: "Claude Code"
    binary: str     # the command's name on PATH
    setting: str    # the setting that names the binary when it is not on PATH

    def argv(self, binary: str, session_id: str) -> list[str]:
        raise NotImplementedError


class ClaudeCode(Runner):
    def argv(self, binary: str, session_id: str) -> list[str]:
        # The prompt goes in on stdin. "auto" has a classifier check each
        # action; stream-json with --verbose keeps every event in the log.
        return [binary, '-p', '--permission-mode', 'auto', '--session-id', session_id,
                '--output-format', 'stream-json', '--verbose']


CLAUDE_CODE = ClaudeCode('claude-code', 'Claude Code', 'claude', 'dispatch.claude_code.command')
RUNNERS = {runner.client: runner for runner in (CLAUDE_CODE,)}


def get(client: str) -> Runner:
    try:
        return RUNNERS[client]
    except KeyError:
        raise LookupError(f'No Dispatcher runner for {client}: available are {", ".join(sorted(RUNNERS))}') from None


def find(runner: Runner) -> str | None:
    """The client's binary on this system: the runner's setting when set, else PATH."""
    from ..config_schema import resolve
    configured = str(resolve(runner.setting)[0] or '').strip()
    if configured:
        return configured if os.path.isfile(configured) else shutil.which(configured)
    return shutil.which(runner.binary)


def check(argv: list[str], env: dict | None = None) -> list[str]:
    """``argv`` when it holds none of the refused flags; ValueError otherwise."""
    refused = [arg for arg in argv[1:] if arg in REFUSED or arg.split('=', 1)[0] in REFUSED
               or arg.split('=', 1)[-1] in REFUSED]
    refused += sorted(name for name in REFUSED_ENV if (env or {}).get(name))
    if refused:
        raise ValueError(f'A Dispatched session never runs with {", ".join(refused)} (ADR 0222)')
    return argv


def command(runner: Runner, session_id: str) -> list[str]:
    """The checked command line that starts ``runner``'s client; LookupError
    when its binary is not found."""
    binary = find(runner)
    if not binary:
        raise LookupError(f'{runner.label} was not found: put {runner.binary} on PATH or set {runner.setting}')
    return check(runner.argv(binary, session_id))
